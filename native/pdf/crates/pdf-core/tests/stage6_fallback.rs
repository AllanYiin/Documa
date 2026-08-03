use pdf_core::{PdfDocument, TextExtractionOptions};

fn fallback_pdf() -> Vec<u8> {
    let content = b"BT /F1 10 Tf 10 10 Td (Price \\200) Tj ET";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 \
          /Resources << /Font << /F1 5 0 R >> >> >>"
            .to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>".to_vec(),
        {
            let mut stream = format!("<< /Length {} >>\nstream\n", content.len()).into_bytes();
            stream.extend_from_slice(content);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica \
          /Encoding /WinAnsiEncoding >>"
            .to_vec(),
    ];
    classic_pdf(&objects)
}

fn indirect_length_pdf() -> Vec<u8> {
    let content = b"BT ET";
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>".to_vec(),
        {
            let mut stream = b"<< /Length 5 0 R >>\nstream\n".to_vec();
            stream.extend_from_slice(content);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
        content.len().to_string().into_bytes(),
    ];
    classic_pdf(&objects)
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
fn inherits_resources_and_reports_explicit_fallback_warning() {
    let document = PdfDocument::parse(&fallback_pdf()).expect("valid fallback PDF");
    let result = document
        .extract_text(TextExtractionOptions::default())
        .expect("fallback extraction");
    assert!(result.text.contains("Price €"));
    assert!(
        result
            .warnings
            .iter()
            .any(|warning| warning.code == "font_fallback_encoding")
    );
}

#[test]
fn resolves_indirect_stream_length_with_cycle_protection() {
    let document = PdfDocument::parse(&indirect_length_pdf()).expect("valid indirect Length PDF");
    let page = document.pages().expect("page tree").remove(0);
    assert_eq!(
        document
            .decoded_page_content(&page)
            .expect("indirect Length resolves"),
        b"BT ET"
    );
}
