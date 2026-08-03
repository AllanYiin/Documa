use std::io::Write;

use flate2::{Compression, write::ZlibEncoder};
use pdf_core::{PdfDocument, TextExtractionOptions};

fn zlib(data: &[u8]) -> Vec<u8> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data).expect("compress fixture");
    encoder.finish().expect("finish fixture")
}

fn set_xref_row(rows: &mut [u8], index: usize, kind: u8, field1: u32, field2: u16) {
    let start = index * 7;
    rows[start] = kind;
    rows[start + 1..start + 5].copy_from_slice(&field1.to_be_bytes());
    rows[start + 5..start + 7].copy_from_slice(&field2.to_be_bytes());
}

#[test]
fn highly_compressible_xref_stream_uses_structural_budget() {
    const ENTRY_COUNT: usize = 5_000;

    let mut pdf = b"%PDF-1.7\n".to_vec();
    let catalog_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog >>\nendobj\n");
    let xref_offset = pdf.len();

    let mut rows = vec![0_u8; ENTRY_COUNT * 7];
    set_xref_row(&mut rows, 0, 0, 0, u16::MAX);
    set_xref_row(
        &mut rows,
        1,
        1,
        u32::try_from(catalog_offset).expect("catalog offset"),
        0,
    );
    set_xref_row(
        &mut rows,
        2,
        1,
        u32::try_from(xref_offset).expect("xref offset"),
        0,
    );
    let compressed = zlib(&rows);
    assert!(
        rows.len() > compressed.len() * 200,
        "fixture must exceed the generic expansion-ratio limit"
    );

    pdf.extend_from_slice(
        format!(
            "2 0 obj\n<< /Type /XRef /Size {ENTRY_COUNT} /Root 1 0 R \
             /W [1 4 2] /Index [0 {ENTRY_COUNT}] /Length {} \
             /Filter /FlateDecode >>\nstream\n",
            compressed.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&compressed);
    pdf.extend_from_slice(
        format!("\nendstream\nendobj\nstartxref\n{xref_offset}\n%%EOF\n").as_bytes(),
    );

    let document = PdfDocument::parse(&pdf).expect("structurally bounded xref stream");
    assert_eq!(
        document.summary().expect("summary").xref_entries,
        ENTRY_COUNT
    );
    document.catalog().expect("catalog");
}

fn standard_cmap_pdf() -> Vec<u8> {
    let content = b"BT /F1 12 Tf 10 700 Td <4142> Tj ET";
    let cmap = br"/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /Adobe-Identity-UCS def
/CMapType 2 def
1 begincodespacerange <00> <ff> endcodespacerange
2 beginbfchar <41> <4f60> <42> <df0fd835> endbfchar
endcmap
CMapName currentdict /CMap defineresource pop
end
end";
    let objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        {
            let mut stream = format!("<< /Length {} >>\nstream\n", content.len()).into_bytes();
            stream.extend_from_slice(content);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R /Encoding 7 0 R >>"
            .to_vec(),
        {
            let mut stream = format!("<< /Length {} >>\nstream\n", cmap.len()).into_bytes();
            stream.extend_from_slice(cmap);
            stream.extend_from_slice(b"\nendstream");
            stream
        },
        b"<< /Type /Encoding /BaseEncoding /WinAnsiEncoding >>".to_vec(),
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
fn standard_cmap_dictionary_delimiters_are_not_hex_strings() {
    let document = PdfDocument::parse(&standard_cmap_pdf()).expect("valid PDF");
    let extracted = document
        .extract_text(TextExtractionOptions::default())
        .expect("standard ToUnicode CMap");

    assert_eq!(extracted.text, "你\u{fffd}");
    assert_eq!(extracted.warnings.len(), 1);
    assert_eq!(extracted.warnings[0].code, "unicode_mapping_invalid");
}
