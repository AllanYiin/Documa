use std::io::Write;

use flate2::{Compression, write::ZlibEncoder};
use pdf_core::{
    ErrorCode, ObjectId, ParseLimits, PdfDictionary, PdfDocument, PdfName, PdfObject, PdfStream,
    decode_stream, decode_stream_with_limits,
};

fn zlib(data: &[u8]) -> Vec<u8> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data).expect("compress fixture");
    encoder.finish().expect("finish fixture")
}

#[test]
fn document_stream_parsing_and_flate_pipeline_integrate() {
    let content = b"BT /F1 12 Tf (Stage 2) Tj ET";
    let compressed = zlib(content);
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let catalog_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog >>\nendobj\n");
    let stream_offset = pdf.len();
    pdf.extend_from_slice(
        format!(
            "2 0 obj\n<< /Length {} /Filter /FlateDecode >>\nstream\n",
            compressed.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&compressed);
    pdf.extend_from_slice(b"\nendstream\nendobj\n");
    let xref_offset = pdf.len();
    pdf.extend_from_slice(
        format!(
            "xref\n0 3\n0000000000 65535 f\n{catalog_offset:010} 00000 n\n\
             {stream_offset:010} 00000 n\ntrailer\n<< /Size 3 /Root 1 0 R >>\n\
             startxref\n{xref_offset}\n%%EOF\n"
        )
        .as_bytes(),
    );

    let document = PdfDocument::parse(&pdf).expect("valid PDF");
    let object = document
        .object(ObjectId::new(2, 0))
        .expect("stream object resolves");
    let PdfObject::Stream(stream) = object.value else {
        panic!("expected stream object");
    };
    assert_eq!(
        decode_stream(&stream).expect("FlateDecode succeeds"),
        content
    );
}

#[test]
fn executes_filter_arrays_in_declared_order() {
    let original = b"two layers";
    let twice_compressed = zlib(&zlib(original));
    let mut dictionary = PdfDictionary::new();
    dictionary.insert(
        PdfName(b"Filter".to_vec()),
        PdfObject::Array(vec![
            PdfObject::Name(PdfName(b"FlateDecode".to_vec())),
            PdfObject::Name(PdfName(b"Fl".to_vec())),
        ]),
    );
    dictionary.insert(
        PdfName(b"DecodeParms".to_vec()),
        PdfObject::Array(vec![PdfObject::Null, PdfObject::Null]),
    );
    let stream = PdfStream {
        dictionary,
        data: twice_compressed,
    };
    assert_eq!(decode_stream(&stream).expect("filter chain"), original);
}

#[test]
fn bounds_filter_chain_depth_before_decoding() {
    let mut dictionary = PdfDictionary::new();
    dictionary.insert(
        PdfName(b"Filter".to_vec()),
        PdfObject::Array(vec![
            PdfObject::Name(PdfName(b"FlateDecode".to_vec())),
            PdfObject::Name(PdfName(b"FlateDecode".to_vec())),
        ]),
    );
    let stream = PdfStream {
        dictionary,
        data: zlib(b"irrelevant"),
    };
    let limits = ParseLimits {
        max_filter_chain_depth: 1,
        ..ParseLimits::default()
    };
    let error =
        decode_stream_with_limits(&stream, &limits).expect_err("chain depth must be bounded");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn malformed_flate_data_has_stable_error_code() {
    let mut dictionary = PdfDictionary::new();
    dictionary.insert(
        PdfName(b"Filter".to_vec()),
        PdfObject::Name(PdfName(b"FlateDecode".to_vec())),
    );
    let stream = PdfStream {
        dictionary,
        data: b"not a zlib stream".to_vec(),
    };
    let error = decode_stream(&stream).expect_err("malformed zlib must fail");
    assert_eq!(error.code, ErrorCode::InvalidStream);
}
