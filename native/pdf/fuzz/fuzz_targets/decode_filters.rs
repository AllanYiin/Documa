#![no_main]
mod limits;
use libfuzzer_sys::fuzz_target;

fuzz_target!(|data: &[u8]| {
    let Some((&selector, data)) = data.split_first() else {
        return;
    };
    if data.len() > 262_144 {
        return;
    }
    let filters = [
        "ASCIIHexDecode",
        "ASCII85Decode",
        "RunLengthDecode",
        "LZWDecode",
    ];
    let mut dictionary = pdf_core::PdfDictionary::new();
    dictionary.insert(
        pdf_core::PdfName(b"Filter".to_vec()),
        pdf_core::PdfObject::Name(pdf_core::PdfName(
            filters[usize::from(selector % 4)].as_bytes().to_vec(),
        )),
    );
    let mut parameters = pdf_core::PdfDictionary::new();
    parameters.insert(
        pdf_core::PdfName(b"EarlyChange".to_vec()),
        pdf_core::PdfObject::Integer(i64::from((selector >> 2) & 1)),
    );
    dictionary.insert(
        pdf_core::PdfName(b"DecodeParms".to_vec()),
        pdf_core::PdfObject::Dictionary(parameters),
    );
    let stream = pdf_core::PdfStream {
        dictionary,
        data: data.to_vec(),
    };
    let _ = pdf_core::decode_stream_with_limits(&stream, &limits::bounded());
});
