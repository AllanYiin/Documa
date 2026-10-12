use std::{fmt::Write as _, io::Write};

use flate2::{Compression, write::ZlibEncoder};
use pdf_core::{
    ErrorCode, ParseLimits, PdfDictionary, PdfName, PdfObject, PdfStream, decode_stream,
    decode_stream_with_limits,
};

fn stream(filter: &str, data: &[u8]) -> PdfStream {
    let mut dictionary = PdfDictionary::new();
    dictionary.insert(
        PdfName(b"Filter".to_vec()),
        PdfObject::Name(PdfName(filter.as_bytes().to_vec())),
    );
    PdfStream {
        dictionary,
        data: data.to_vec(),
    }
}

#[test]
fn ascii_filters_decode_whitespace_odd_nibbles_zero_runs_and_partial_groups() {
    assert_eq!(
        decode_stream(&stream("AHx", b" 61\t62\r6>ignored")).unwrap(),
        b"ab`"
    );
    assert_eq!(
        decode_stream(&stream("ASCII85Decode", b"87cURD_*#TDfTZ)+T~>")).unwrap(),
        b"Hello, world!"
    );
    assert_eq!(
        decode_stream(&stream("A85", b"z\n!!~>")).unwrap(),
        vec![0; 5]
    );
    for (filter, bytes) in [("AHx", b">".as_slice()), ("A85", b"~>"), ("RL", &[128])] {
        assert!(decode_stream(&stream(filter, bytes)).unwrap().is_empty());
    }
}

#[test]
fn run_length_decodes_literal_and_repeat_boundaries() {
    let bytes = [2, b'A', b'B', b'C', 255, b'D', 129, b'E', 128];
    let mut expected = b"ABCDD".to_vec();
    expected.extend_from_slice(&[b'E'; 128]);
    assert_eq!(
        decode_stream(&stream("RunLengthDecode", &bytes)).unwrap(),
        expected
    );
}

#[test]
fn invalid_and_truncated_filters_return_stable_errors() {
    for (filter, bytes) in [
        ("AHx", b"A".as_slice()),
        ("AHx", b"AG>"),
        ("A85", b"!~>"),
        ("A85", b"uuuuu~>"),
        ("A85", b"!z~>"),
        ("A85", b"!!!!!"),
        ("A85", b"~ >"),
        ("A85", b"v~>"),
        ("RL", &[2, 1]),
        ("RL", &[255]),
        ("RL", &[0, 1]),
        ("LZW", &[0]),
        ("LZW", &[0x80, 0x00]),
    ] {
        assert_eq!(
            decode_stream(&stream(filter, bytes)).unwrap_err().code,
            ErrorCode::InvalidStream,
            "{filter}: {bytes:?}"
        );
    }
}

#[test]
fn new_filters_honor_absolute_ratio_and_document_budgets() {
    for (filter, data) in [
        ("AHx", b"41414141>".as_slice()),
        ("A85", b"z~>"),
        ("RL", &[253, b'A', 128]),
    ] {
        let input = stream(filter, data);
        let exact = ParseLimits {
            max_decoded_stream_bytes: 4,
            max_total_decoded_bytes: 4,
            ..ParseLimits::default()
        };
        assert_eq!(decode_stream_with_limits(&input, &exact).unwrap().len(), 4);
        for limits in [
            ParseLimits {
                max_decoded_stream_bytes: 3,
                ..exact.clone()
            },
            ParseLimits {
                max_total_decoded_bytes: 3,
                ..exact.clone()
            },
            ParseLimits {
                max_stream_expansion_ratio: 0,
                ..exact.clone()
            },
        ] {
            assert_eq!(
                decode_stream_with_limits(&input, &limits).unwrap_err().code,
                ErrorCode::LimitExceeded
            );
        }
    }
}

#[test]
fn asciihex_then_flate_chains_and_charges_each_stage() {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(b"chain payload").unwrap();
    let compressed = encoder.finish().unwrap();
    let mut hex = String::new();
    for byte in &compressed {
        write!(hex, "{byte:02x}").unwrap();
    }
    hex.push('>');
    let mut input = stream("AHx", hex.as_bytes());
    input.dictionary.insert(
        PdfName(b"Filter".to_vec()),
        PdfObject::Array(vec![
            PdfObject::Name(PdfName(b"AHx".to_vec())),
            PdfObject::Name(PdfName(b"Fl".to_vec())),
        ]),
    );
    assert_eq!(decode_stream(&input).unwrap(), b"chain payload");
    let limits = ParseLimits {
        max_total_decoded_bytes: compressed.len() + 12,
        ..ParseLimits::default()
    };
    assert_eq!(
        decode_stream_with_limits(&input, &limits).unwrap_err().code,
        ErrorCode::LimitExceeded
    );
}

#[test]
fn lzw_decodes_iso_32000_example_and_bounds_output() {
    // ISO 32000-1 section 7.4.4.2, example 2. Includes code == next-entry case.
    let input = stream(
        "LZWDecode",
        &[0x80, 0x0b, 0x60, 0x50, 0x22, 0x0c, 0x0c, 0x85, 0x01],
    );
    assert_eq!(
        decode_stream(&input).unwrap(),
        [45, 45, 45, 45, 45, 65, 45, 45, 45, 66]
    );
    let limits = ParseLimits {
        max_decoded_stream_bytes: 9,
        ..ParseLimits::default()
    };
    assert_eq!(
        decode_stream_with_limits(&input, &limits).unwrap_err().code,
        ErrorCode::LimitExceeded
    );
    for end in 0..input.data.len() {
        let truncated = stream("LZW", &input.data[..end]);
        assert!(decode_stream(&truncated).is_err());
    }
}

fn pack_codes(codes: &[(u16, usize)]) -> Vec<u8> {
    let mut bits = Vec::new();
    for &(code, width) in codes {
        for shift in (0..width).rev() {
            bits.push(u8::try_from((code >> shift) & 1).unwrap());
        }
    }
    bits.resize(bits.len().div_ceil(8) * 8, 0);
    bits.chunks(8)
        .map(|chunk| chunk.iter().fold(0, |value, bit| (value << 1) | bit))
        .collect()
}

#[test]
fn lzw_width_transitions_clear_and_predictor_work_for_both_early_change_modes() {
    for early in [0, 1] {
        let mut codes = vec![(256, 9)];
        let mut expected = Vec::new();
        for index in 0..3000 {
            let width = if index < 255 - early {
                9
            } else if index < 767 - early {
                10
            } else if index < 1791 - early {
                11
            } else {
                12
            };
            let byte = u8::try_from(index % 256).unwrap();
            codes.push((u16::from(byte), width));
            expected.push(byte);
        }
        codes.extend_from_slice(&[(256, 12), (65, 9), (257, 9)]);
        expected.push(b'A');
        let mut input = stream("LZW", &pack_codes(&codes));
        let mut parameters = PdfDictionary::new();
        parameters.insert(
            PdfName(b"EarlyChange".to_vec()),
            PdfObject::Integer(i64::from(early)),
        );
        input.dictionary.insert(
            PdfName(b"DecodeParms".to_vec()),
            PdfObject::Dictionary(parameters),
        );
        assert_eq!(decode_stream(&input).unwrap(), expected);
    }
    let mut input = stream(
        "LZW",
        &pack_codes(&[(256, 9), (10, 9), (10, 9), (10, 9), (257, 9)]),
    );
    let mut parameters = PdfDictionary::new();
    parameters.insert(PdfName(b"Predictor".to_vec()), PdfObject::Integer(2));
    parameters.insert(PdfName(b"Columns".to_vec()), PdfObject::Integer(3));
    input.dictionary.insert(
        PdfName(b"DecodeParms".to_vec()),
        PdfObject::Dictionary(parameters),
    );
    assert_eq!(decode_stream(&input).unwrap(), [10, 20, 30]);
}
