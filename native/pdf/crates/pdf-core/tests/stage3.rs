use std::io::Write;

use flate2::{Compression, write::ZlibEncoder};
use pdf_core::{ErrorCode, ObjectId, PdfDocument, PdfObject, XrefKind};

fn zlib(data: &[u8]) -> Vec<u8> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data).expect("compress fixture");
    encoder.finish().expect("finish fixture")
}

fn push_xref_row(rows: &mut Vec<u8>, kind: u8, field1: u32, field2: u16) {
    rows.push(kind);
    rows.extend_from_slice(&field1.to_be_bytes());
    rows.extend_from_slice(&field2.to_be_bytes());
}

fn xref_stream_pdf(packed_number: u32) -> Vec<u8> {
    xref_stream_pdf_with_padding(packed_number, 0)
}

fn xref_stream_pdf_with_padding(packed_number: u32, padding: usize) -> Vec<u8> {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let catalog_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Payload 2 0 R >>\nendobj\n");

    let object_stream_offset = pdf.len();
    let mut packed = format!("{packed_number} 0 << /Answer 42 /Text (packed) >>").into_bytes();
    packed.resize(packed.len() + padding, b' ');
    let packed = zlib(&packed);
    pdf.extend_from_slice(
        format!(
            "4 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Length {} \
             /Filter /FlateDecode >>\nstream\n",
            packed.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&packed);
    pdf.extend_from_slice(b"\nendstream\nendobj\n");

    let xref_offset = pdf.len();
    let mut rows = Vec::new();
    push_xref_row(&mut rows, 0, 0, u16::MAX);
    push_xref_row(
        &mut rows,
        1,
        u32::try_from(catalog_offset).expect("fixture offset"),
        0,
    );
    push_xref_row(&mut rows, 2, 4, 0);
    push_xref_row(
        &mut rows,
        1,
        u32::try_from(xref_offset).expect("fixture offset"),
        0,
    );
    push_xref_row(
        &mut rows,
        1,
        u32::try_from(object_stream_offset).expect("fixture offset"),
        0,
    );
    let rows = zlib(&rows);
    pdf.extend_from_slice(
        format!(
            "3 0 obj\n<< /Type /XRef /Size 5 /Root 1 0 R /W [1 4 2] \
             /Index [0 5] /Length {} /Filter /FlateDecode >>\nstream\n",
            rows.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&rows);
    pdf.extend_from_slice(
        format!("\nendstream\nendobj\nstartxref\n{xref_offset}\n%%EOF\n").as_bytes(),
    );
    pdf
}

#[test]
fn resolves_compressed_object_from_flate_xref_and_object_streams() {
    let document = PdfDocument::parse(&xref_stream_pdf(2)).expect("valid PDF 1.5 structures");
    let entry = document
        .xref_entries()
        .get(&2)
        .expect("compressed xref entry");
    assert_eq!(entry.kind, XrefKind::Compressed);
    assert_eq!(entry.object_stream, Some(4));
    assert_eq!(entry.object_index, Some(0));

    let packed = document
        .object(ObjectId::new(2, 0))
        .expect("compressed object resolves");
    assert_eq!(
        packed.value.get(b"Answer").and_then(PdfObject::as_integer),
        Some(42)
    );
    assert!(matches!(
        packed.value.get(b"Text"),
        Some(PdfObject::String(value)) if value.0 == b"packed"
    ));

    let after_first = document.decode_metrics();
    document
        .object(ObjectId::new(2, 0))
        .expect("cached compressed object resolves again");
    let after_second = document.decode_metrics();
    assert_eq!(after_second.decoded_bytes, after_first.decoded_bytes);
    assert_eq!(
        after_second.decode_operations,
        after_first.decode_operations
    );
    assert_eq!(after_second.object_stream_cache_hits, 1);
    assert_eq!(after_second.object_stream_cache_misses, 1);
}

#[test]
fn highly_compressible_object_stream_uses_structural_budget() {
    let document = PdfDocument::parse(&xref_stream_pdf_with_padding(2, 100_000))
        .expect("valid highly compressible object stream");
    let object = document
        .object(ObjectId::new(2, 0))
        .expect("compressed object resolves above the generic ratio heuristic");

    assert_eq!(
        object.value.get(b"Answer").and_then(PdfObject::as_integer),
        Some(42)
    );
}

#[test]
fn compressed_xref_id_must_match_object_stream_header() {
    let document = PdfDocument::parse(&xref_stream_pdf(5)).expect("xref syntax is valid");
    let error = document
        .object(ObjectId::new(2, 0))
        .expect_err("object stream header mismatch must fail");
    assert_eq!(error.code, ErrorCode::ObjectIdMismatch);
}

#[test]
fn merges_hybrid_xref_stream_entries() {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let catalog_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Payload 2 0 R >>\nendobj\n");

    let object_stream_offset = pdf.len();
    let packed = zlib(b"2 0 << /Hybrid true >>");
    pdf.extend_from_slice(
        format!(
            "4 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Length {} \
             /Filter /FlateDecode >>\nstream\n",
            packed.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&packed);
    pdf.extend_from_slice(b"\nendstream\nendobj\n");

    let hybrid_offset = pdf.len();
    let mut row = Vec::new();
    push_xref_row(&mut row, 2, 4, 0);
    pdf.extend_from_slice(
        format!(
            "3 0 obj\n<< /Type /XRef /Size 5 /W [1 4 2] /Index [2 1] \
             /Length {} >>\nstream\n",
            row.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&row);
    pdf.extend_from_slice(b"\nendstream\nendobj\n");

    let classic_offset = pdf.len();
    pdf.extend_from_slice(b"xref\n0 2\n0000000000 65535 f\n");
    pdf.extend_from_slice(format!("{catalog_offset:010} 00000 n\n").as_bytes());
    pdf.extend_from_slice(b"3 2\n");
    pdf.extend_from_slice(format!("{hybrid_offset:010} 00000 n\n").as_bytes());
    pdf.extend_from_slice(format!("{object_stream_offset:010} 00000 n\n").as_bytes());
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size 5 /Root 1 0 R /XRefStm {hybrid_offset} >>\n\
             startxref\n{classic_offset}\n%%EOF\n"
        )
        .as_bytes(),
    );

    let document = PdfDocument::parse(&pdf).expect("valid hybrid-reference PDF");
    let object = document
        .object(ObjectId::new(2, 0))
        .expect("hybrid compressed object resolves");
    assert_eq!(object.value.get(b"Hybrid"), Some(&PdfObject::Boolean(true)));
}
