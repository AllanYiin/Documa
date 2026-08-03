use std::io::Write;

use flate2::{Compression, write::ZlibEncoder};
use pdf_core::{ParseLimits, PdfDocument, TextExtractionOptions, parse_content};

fn zlib(data: &[u8]) -> Vec<u8> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data).expect("compress fixture");
    encoder.finish().expect("finish fixture")
}

fn text_pdf() -> Vec<u8> {
    let cmap = br"
        /CIDInit /ProcSet findresource begin
        1 begincodespacerange <00> <ff> endcodespacerange
        3 beginbfchar
        <01> <4f60>
        <02> <597d>
        <03> <00650301>
        endbfchar
        end
    ";
    let content = zlib(b"BT /F1 12 Tf 1 0 0 1 10 700 Tm <0102> Tj 0 -20 Td <03> Tj ET");
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        {
            let mut stream = format!(
                "<< /Length {} /Filter /FlateDecode >>\nstream\n",
                content.len()
            )
            .into_bytes();
            stream.extend_from_slice(&content);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
        b"<< /Type /Font /Subtype /Type1 /BaseFont /FixtureFont /ToUnicode 6 0 R >>".to_vec(),
        {
            let mut stream = format!("<< /Length {} >>\nstream\n", cmap.len()).into_bytes();
            stream.extend_from_slice(cmap);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
    ];

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
fn traverses_pages_decodes_content_and_extracts_tounicode_text() {
    let document = PdfDocument::parse(&text_pdf()).expect("valid text PDF");
    let pages = document.pages().expect("page tree");
    assert_eq!(pages.len(), 1);
    assert_eq!(pages[0].media_box, Some([0.0, 0.0, 612.0, 792.0]));

    let content = document
        .decoded_page_content(&pages[0])
        .expect("decoded Flate content");
    let operations = parse_content(&content, &ParseLimits::default()).expect("content operators");
    assert!(
        operations
            .iter()
            .any(|operation| operation.operator == b"Tj")
    );

    let raw = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: true,
        })
        .expect("raw Unicode extraction");
    assert_eq!(raw.pages[0].spans.len(), 2);
    assert!(raw.text.contains("你好"));
    assert!(raw.text.contains("e\u{301}"));
    assert!(raw.warnings.is_empty(), "ToUnicode should be authoritative");

    let normalized = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: true,
            layout: true,
        })
        .expect("normalized Unicode extraction");
    assert!(normalized.text.contains('é'));
    assert!(!normalized.text.contains("e\u{301}"));
}
