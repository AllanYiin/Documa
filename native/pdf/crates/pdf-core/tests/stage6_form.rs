use pdf_core::{PdfDocument, TextExtractionOptions};

fn form_pdf() -> Vec<u8> {
    let page_content = b"/X1 Do";
    let form_content = b"BT /F1 10 Tf 5 7 Td (Form text) Tj ET";
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << \
          /Font << /F1 5 0 R >> /XObject << /X1 6 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_dictionary(page_content, b""),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
        stream_dictionary(
            form_content,
            b"/Type /XObject /Subtype /Form /Matrix [1 0 0 1 20 30]",
        ),
    ];
    classic_pdf(&objects)
}

fn stream_dictionary(data: &[u8], extra: &[u8]) -> Vec<u8> {
    let mut stream = format!("<< /Length {} ", data.len()).into_bytes();
    stream.extend_from_slice(extra);
    stream.extend_from_slice(b" >>\nstream\n");
    stream.extend_from_slice(data);
    stream.extend_from_slice(b"\nendstream");
    stream
}

fn classic_pdf(objects: &[Vec<u8>]) -> Vec<u8> {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(object);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f\n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n\n").as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n",
            objects.len() + 1
        )
        .as_bytes(),
    );
    pdf
}

#[test]
fn extracts_text_from_form_xobject_and_applies_form_matrix() {
    let document = PdfDocument::parse(&form_pdf()).expect("valid Form XObject PDF");
    let result = document
        .extract_text(TextExtractionOptions::default())
        .expect("Form text extraction");
    assert_eq!(result.text, "Form text");
    assert_eq!(result.pages[0].spans.len(), 1);
    assert!((result.pages[0].spans[0].x - 25.0).abs() < f64::EPSILON);
    assert!((result.pages[0].spans[0].y - 37.0).abs() < f64::EPSILON);
}
