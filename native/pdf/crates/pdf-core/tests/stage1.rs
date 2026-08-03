use pdf_core::{ErrorCode, ObjectId, ParseLimits, PdfDocument, PdfObject};

fn append_xref(pdf: &mut Vec<u8>, first: u32, offsets: &[usize], previous: Option<usize>) -> usize {
    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n{first} {}\n", offsets.len()).as_bytes());
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n\n").as_bytes());
    }
    let size = usize::try_from(first).expect("fixture object number") + offsets.len();
    pdf.extend_from_slice(format!("trailer\n<< /Size {size} /Root 1 0 R").as_bytes());
    if let Some(previous) = previous {
        pdf.extend_from_slice(format!(" /Prev {previous}").as_bytes());
    }
    pdf.extend_from_slice(format!(" >>\nstartxref\n{xref_offset}\n%%EOF\n").as_bytes());
    xref_offset
}

#[test]
fn newest_incremental_xref_entry_wins() {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let old_object = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Marker (old) >>\nendobj\n");
    let first_xref = pdf.len();
    pdf.extend_from_slice(b"xref\n0 2\n0000000000 65535 f\n");
    pdf.extend_from_slice(format!("{old_object:010} 00000 n\n").as_bytes());
    pdf.extend_from_slice(
        format!("trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n{first_xref}\n%%EOF\n").as_bytes(),
    );

    let new_object = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Marker (new) >>\nendobj\n");
    append_xref(&mut pdf, 1, &[new_object], Some(first_xref));

    let document = PdfDocument::parse(&pdf).expect("valid incremental PDF");
    let catalog = document.catalog().expect("new catalog resolves");
    assert!(
        matches!(
            catalog.value.get(b"Marker"),
            Some(PdfObject::String(value)) if value.0 == b"new"
        ),
        "newest revision must mask the old object offset"
    );
    assert_eq!(document.summary().expect("summary").revisions, 2);
}

#[test]
fn detects_cyclic_prev_chain() {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let object_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog >>\nendobj\n");
    let xref_offset = pdf.len();
    pdf.extend_from_slice(
        format!(
            "xref\n0 2\n0000000000 65535 f\n{object_offset:010} 00000 n\n\
             trailer\n<< /Size 2 /Root 1 0 R /Prev {xref_offset} >>\n\
             startxref\n{xref_offset}\n%%EOF\n"
        )
        .as_bytes(),
    );
    let error = PdfDocument::parse(&pdf).expect_err("Prev cycle must fail");
    assert_eq!(error.code, ErrorCode::InvalidXref);
}

#[test]
fn object_identifier_mismatch_is_reported() {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let object_offset = pdf.len();
    pdf.extend_from_slice(b"2 0 obj\n<< /Type /Catalog >>\nendobj\n");
    let xref_offset = pdf.len();
    pdf.extend_from_slice(
        format!(
            "xref\n0 2\n0000000000 65535 f\n{object_offset:010} 00000 n\n\
             trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
        )
        .as_bytes(),
    );
    let document = PdfDocument::parse(&pdf).expect("xref syntax is valid");
    let error = document
        .object(ObjectId::new(1, 0))
        .expect_err("declared object id must match xref");
    assert_eq!(error.code, ErrorCode::ObjectIdMismatch);
}

#[test]
fn rejects_input_before_allocating_parser_state_past_limit() {
    let limits = ParseLimits {
        max_file_bytes: 4,
        ..ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(b"%PDF-1.7", limits)
        .expect_err("file limit must be enforced");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}
