use std::{collections::BTreeMap, fmt::Write as _, io::Write as _};

use flate2::{Compression, write::ZlibEncoder};
use pdf_core::{
    ErrorCode, ObjectId, ParseLimits, PdfDictionary, PdfDocument, PdfName, PdfObject, PdfStream,
    decode_stream_with_limits,
};

#[derive(Clone)]
struct PackedStreamSpec {
    number: u32,
    members: Vec<(u32, &'static [u8])>,
    padding: usize,
    declared_n: Option<usize>,
    declared_first: Option<usize>,
    cyclic_length: bool,
}

impl PackedStreamSpec {
    fn new(number: u32, members: Vec<(u32, &'static [u8])>) -> Self {
        Self {
            number,
            members,
            padding: 0,
            declared_n: None,
            declared_first: None,
            cyclic_length: false,
        }
    }
}

struct Fixture {
    bytes: Vec<u8>,
    xref_decoded_bytes: usize,
    object_stream_decoded_bytes: BTreeMap<u32, usize>,
}

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

#[allow(clippy::too_many_lines)] // The fixture builder mirrors the xref/object-stream wire format.
fn build_fixture(streams: &[PackedStreamSpec]) -> Fixture {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let catalog_offset = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog >>\nendobj\n");

    let mut stream_offsets = BTreeMap::new();
    let mut object_stream_decoded_bytes = BTreeMap::new();
    let mut compressed_entries = BTreeMap::new();
    for spec in streams {
        let mut member_data = Vec::new();
        let mut header = String::new();
        for (index, (number, value)) in spec.members.iter().enumerate() {
            write!(header, "{number} {} ", member_data.len()).expect("write fixture header");
            member_data.extend_from_slice(value);
            if index + 1 != spec.members.len() {
                member_data.push(b' ');
            }
            compressed_entries.insert(
                *number,
                (
                    spec.number,
                    u16::try_from(index).expect("fixture member index"),
                ),
            );
        }
        member_data.resize(member_data.len() + spec.padding, b' ');
        let first = spec.declared_first.unwrap_or(header.len());
        let declared_n = spec.declared_n.unwrap_or(spec.members.len());
        let mut decoded = header.into_bytes();
        decoded.extend_from_slice(&member_data);
        object_stream_decoded_bytes.insert(spec.number, decoded.len());
        let encoded = zlib(&decoded);

        let offset = pdf.len();
        stream_offsets.insert(spec.number, offset);
        let length = if spec.cyclic_length {
            format!("{} 0 R", spec.number)
        } else {
            encoded.len().to_string()
        };
        pdf.extend_from_slice(
            format!(
                "{} 0 obj\n<< /Type /ObjStm /N {declared_n} /First {first} /Length {length} \
                 /Filter /FlateDecode >>\nstream\n",
                spec.number
            )
            .as_bytes(),
        );
        pdf.extend_from_slice(&encoded);
        pdf.extend_from_slice(b"\nendstream\nendobj\n");
    }

    let maximum_number = streams
        .iter()
        .flat_map(|stream| {
            std::iter::once(stream.number).chain(stream.members.iter().map(|(number, _)| *number))
        })
        .max()
        .unwrap_or(1);
    let xref_number = maximum_number.checked_add(1).expect("fixture xref number");
    let size = xref_number.checked_add(1).expect("fixture xref size");
    let xref_offset = pdf.len();
    let mut rows = Vec::new();
    for number in 0..size {
        if number == 0 {
            push_xref_row(&mut rows, 0, 0, u16::MAX);
        } else if number == 1 {
            push_xref_row(
                &mut rows,
                1,
                u32::try_from(catalog_offset).expect("catalog offset"),
                0,
            );
        } else if number == xref_number {
            push_xref_row(
                &mut rows,
                1,
                u32::try_from(xref_offset).expect("xref offset"),
                0,
            );
        } else if let Some(&(stream_number, index)) = compressed_entries.get(&number) {
            push_xref_row(&mut rows, 2, stream_number, index);
        } else if let Some(&offset) = stream_offsets.get(&number) {
            push_xref_row(
                &mut rows,
                1,
                u32::try_from(offset).expect("object stream offset"),
                0,
            );
        } else {
            push_xref_row(&mut rows, 0, 0, 0);
        }
    }
    let xref_decoded_bytes = rows.len();
    let encoded_rows = zlib(&rows);
    pdf.extend_from_slice(
        format!(
            "{xref_number} 0 obj\n<< /Type /XRef /Size {size} /Root 1 0 R /W [1 4 2] \
             /Index [0 {size}] /Length {} /Filter /FlateDecode >>\nstream\n",
            encoded_rows.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&encoded_rows);
    pdf.extend_from_slice(
        format!("\nendstream\nendobj\nstartxref\n{xref_offset}\n%%EOF\n").as_bytes(),
    );

    Fixture {
        bytes: pdf,
        xref_decoded_bytes,
        object_stream_decoded_bytes,
    }
}

fn one_stream_fixture() -> Fixture {
    build_fixture(&[PackedStreamSpec::new(
        10,
        vec![(2, b"<< /Value 2 >>"), (3, b"<< /Value 3 >>")],
    )])
}

fn content_stream_pdf(content: &[u8]) -> Vec<u8> {
    let encoded = zlib(content);
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    offsets.push(pdf.len());
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
    offsets.push(pdf.len());
    pdf.extend_from_slice(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n");
    offsets.push(pdf.len());
    pdf.extend_from_slice(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>\nendobj\n",
    );
    offsets.push(pdf.len());
    pdf.extend_from_slice(
        format!(
            "4 0 obj\n<< /Length {} /Filter /FlateDecode >>\nstream\n",
            encoded.len()
        )
        .as_bytes(),
    );
    pdf.extend_from_slice(&encoded);
    pdf.extend_from_slice(b"\nendstream\nendobj\n");
    let xref_offset = pdf.len();
    pdf.extend_from_slice(b"xref\n0 5\n0000000000 65535 f\n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n\n").as_bytes());
    }
    pdf.extend_from_slice(
        format!("trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").as_bytes(),
    );
    pdf
}

#[test]
fn xref_decode_is_part_of_the_document_lifetime_budget() {
    let fixture = one_stream_fixture();
    let exact = ParseLimits {
        max_total_decoded_bytes: fixture.xref_decoded_bytes,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&fixture.bytes, exact).expect("exact boundary");
    assert_eq!(
        document.decode_metrics().decoded_bytes,
        fixture.xref_decoded_bytes
    );

    let too_small = ParseLimits {
        max_total_decoded_bytes: fixture.xref_decoded_bytes - 1,
        ..ParseLimits::default()
    };
    let error = PdfDocument::parse_with_limits(&fixture.bytes, too_small)
        .expect_err("xref decode must exhaust the document budget");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn un_evicted_object_stream_decodes_once_and_clones_share_runtime() {
    let fixture = one_stream_fixture();
    let document = PdfDocument::parse(&fixture.bytes).expect("valid fixture");
    let initial = document.decode_metrics();
    assert_eq!(initial.decode_operations, 1);

    document
        .object(ObjectId::new(2, 0))
        .expect("first compressed member");
    let after_miss = document.decode_metrics();
    assert_eq!(after_miss.object_stream_cache_misses, 1);
    assert_eq!(after_miss.object_stream_cache_hits, 0);
    assert_eq!(after_miss.decode_operations, initial.decode_operations + 1);
    assert_eq!(
        after_miss.decoded_bytes - initial.decoded_bytes,
        fixture.object_stream_decoded_bytes[&10]
    );

    let clone = document.clone();
    let second = clone
        .object(ObjectId::new(3, 0))
        .expect("second compressed member from shared cache");
    assert_eq!(
        second.value.get(b"Value").and_then(PdfObject::as_integer),
        Some(3)
    );
    let after_hit = document.decode_metrics();
    assert_eq!(after_hit.decoded_bytes, after_miss.decoded_bytes);
    assert_eq!(after_hit.decode_operations, after_miss.decode_operations);
    assert_eq!(after_hit.object_stream_cache_hits, 1);
    assert_eq!(after_hit.object_stream_cache_misses, 1);
    assert_eq!(after_hit.object_stream_cache_entries, 1);
    assert!(
        after_hit.peak_object_stream_cache_bytes
            <= ParseLimits::default().max_cached_object_stream_bytes
    );

    let independent = PdfDocument::parse(&fixture.bytes).expect("second document");
    independent
        .object(ObjectId::new(2, 0))
        .expect("second document must perform its own decode");
    let independent_metrics = independent.decode_metrics();
    assert_eq!(independent_metrics.object_stream_cache_hits, 0);
    assert_eq!(independent_metrics.object_stream_cache_misses, 1);
}

#[test]
fn document_budget_accepts_the_exact_object_stream_boundary() {
    let fixture = one_stream_fixture();
    let total = fixture.xref_decoded_bytes + fixture.object_stream_decoded_bytes[&10];
    let exact = ParseLimits {
        max_total_decoded_bytes: total,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&fixture.bytes, exact).expect("xref fits");
    document
        .object(ObjectId::new(2, 0))
        .expect("exact decode boundary");
    document
        .object(ObjectId::new(3, 0))
        .expect("cache hit costs no bytes");
    assert_eq!(document.decode_metrics().decoded_bytes, total);

    let one_byte_short = ParseLimits {
        max_total_decoded_bytes: total - 1,
        ..ParseLimits::default()
    };
    let document =
        PdfDocument::parse_with_limits(&fixture.bytes, one_byte_short).expect("xref still fits");
    let error = document
        .object(ObjectId::new(2, 0))
        .expect_err("one missing byte must fail");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn deterministic_lru_eviction_redecodes_and_recharges() {
    let fixture = build_fixture(&[
        PackedStreamSpec::new(10, vec![(2, b"<< /Value 2 >>")]),
        PackedStreamSpec::new(11, vec![(3, b"<< /Value 3 >>")]),
    ]);
    let limits = ParseLimits {
        max_cached_object_streams: 1,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&fixture.bytes, limits).expect("valid fixture");
    document
        .object(ObjectId::new(2, 0))
        .expect("stream 10 miss");
    document
        .object(ObjectId::new(3, 0))
        .expect("stream 11 evicts stream 10");
    document
        .object(ObjectId::new(2, 0))
        .expect("stream 10 decodes again");

    let metrics = document.decode_metrics();
    assert_eq!(metrics.object_stream_cache_hits, 0);
    assert_eq!(metrics.object_stream_cache_misses, 3);
    assert_eq!(metrics.object_stream_cache_evictions, 2);
    assert_eq!(metrics.object_stream_cache_entries, 1);
    assert_eq!(metrics.peak_object_stream_cache_entries, 1);
    assert_eq!(metrics.decode_operations, 4);
    assert_eq!(
        metrics.decoded_bytes,
        fixture.xref_decoded_bytes
            + fixture.object_stream_decoded_bytes[&10] * 2
            + fixture.object_stream_decoded_bytes[&11]
    );
}

#[test]
fn cache_byte_limit_cannot_bypass_the_document_budget() {
    let fixture = one_stream_fixture();
    let one_decode = fixture.xref_decoded_bytes + fixture.object_stream_decoded_bytes[&10];
    let limits = ParseLimits {
        max_total_decoded_bytes: one_decode,
        max_cached_object_stream_bytes: 1,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&fixture.bytes, limits).expect("xref fits");
    document
        .object(ObjectId::new(2, 0))
        .expect("first decode fits");
    let first = document.decode_metrics();
    assert_eq!(first.object_stream_cache_entries, 0);
    assert_eq!(first.object_stream_cache_misses, 1);

    let error = document
        .object(ObjectId::new(3, 0))
        .expect_err("uncached second decode must be recharged");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
    assert_eq!(document.decode_metrics().object_stream_cache_misses, 2);
}

#[test]
fn cache_byte_limit_has_an_exact_inclusive_boundary() {
    let fixture = one_stream_fixture();
    let probe = PdfDocument::parse(&fixture.bytes).expect("probe document");
    probe
        .object(ObjectId::new(2, 0))
        .expect("populate probe cache");
    let weight = probe.decode_metrics().object_stream_cache_bytes;
    assert!(weight > 0);

    let exact = ParseLimits {
        max_cached_object_stream_bytes: weight,
        ..ParseLimits::default()
    };
    let document =
        PdfDocument::parse_with_limits(&fixture.bytes, exact).expect("exact cache limit");
    document
        .object(ObjectId::new(2, 0))
        .expect("entry fits exactly");
    assert_eq!(document.decode_metrics().object_stream_cache_bytes, weight);

    let short = ParseLimits {
        max_cached_object_stream_bytes: weight - 1,
        ..ParseLimits::default()
    };
    let document =
        PdfDocument::parse_with_limits(&fixture.bytes, short).expect("short cache limit");
    document
        .object(ObjectId::new(2, 0))
        .expect("decode still succeeds uncached");
    assert_eq!(document.decode_metrics().object_stream_cache_entries, 0);
}

#[test]
fn general_content_streams_share_budget_without_structural_ratio_exceptions() {
    let content = b"q Q";
    let pdf = content_stream_pdf(content);
    let limits = ParseLimits {
        max_total_decoded_bytes: content.len(),
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&pdf, limits).expect("classic xref document");
    let page = document.pages().expect("one page").remove(0);
    assert_eq!(
        document
            .decoded_page_content(&page)
            .expect("exact content budget"),
        content
    );
    let error = document
        .decoded_page_content(&page)
        .expect_err("second decode must consume the same lifetime budget");
    assert_eq!(error.code, ErrorCode::LimitExceeded);

    let highly_compressible = vec![b'A'; 100_000];
    let pdf = content_stream_pdf(&highly_compressible);
    let document = PdfDocument::parse(&pdf).expect("classic xref document");
    let page = document.pages().expect("one page").remove(0);
    let error = document
        .decoded_page_content(&page)
        .expect_err("ordinary content must not receive a structural ratio exception");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn single_object_stream_absolute_limit_is_still_authoritative() {
    let mut stream = PackedStreamSpec::new(10, vec![(2, b"<< /Value 2 >>")]);
    stream.padding = 1_000;
    let fixture = build_fixture(&[stream]);
    let decoded = fixture.object_stream_decoded_bytes[&10];
    assert!(decoded > fixture.xref_decoded_bytes);
    let limits = ParseLimits {
        max_decoded_stream_bytes: decoded - 1,
        ..ParseLimits::default()
    };
    let document = PdfDocument::parse_with_limits(&fixture.bytes, limits).expect("xref fits");
    let error = document
        .object(ObjectId::new(2, 0))
        .expect_err("single stream absolute limit must fail");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn large_n_first_and_cycle_paths_are_bounded() {
    let mut large_n = PackedStreamSpec::new(10, vec![(2, b"<< /Value 2 >>")]);
    large_n.declared_n = Some(ParseLimits::default().max_xref_entries + 1);
    let fixture = build_fixture(&[large_n]);
    let document = PdfDocument::parse(&fixture.bytes).expect("xref is valid");
    let error = document
        .object(ObjectId::new(2, 0))
        .expect_err("object stream N must be bounded");
    assert_eq!(error.code, ErrorCode::LimitExceeded);

    let mut large_first = PackedStreamSpec::new(10, vec![(2, b"<< /Value 2 >>")]);
    large_first.declared_first = Some(10_000);
    let fixture = build_fixture(&[large_first]);
    let document = PdfDocument::parse(&fixture.bytes).expect("xref is valid");
    let error = document
        .object(ObjectId::new(2, 0))
        .expect_err("First beyond decoded bytes must fail");
    assert_eq!(error.code, ErrorCode::InvalidStream);

    let mut cyclic = PackedStreamSpec::new(10, vec![(2, b"<< /Value 2 >>")]);
    cyclic.cyclic_length = true;
    let fixture = build_fixture(&[cyclic]);
    let document = PdfDocument::parse(&fixture.bytes).expect("xref is valid");
    let error = document
        .object(ObjectId::new(2, 0))
        .expect_err("cyclic object-stream Length must fail");
    assert_eq!(error.code, ErrorCode::InvalidReference);
}

#[test]
fn predictor_input_and_output_allocations_share_the_total_budget() {
    let encoded_predictor_row = [0_u8, b'a', b'b', b'c'];
    let compressed = zlib(&encoded_predictor_row);
    let mut decode_parameters = PdfDictionary::new();
    decode_parameters.insert(PdfName(b"Predictor".to_vec()), PdfObject::Integer(15));
    decode_parameters.insert(PdfName(b"Colors".to_vec()), PdfObject::Integer(1));
    decode_parameters.insert(PdfName(b"BitsPerComponent".to_vec()), PdfObject::Integer(8));
    decode_parameters.insert(PdfName(b"Columns".to_vec()), PdfObject::Integer(3));
    let mut dictionary = PdfDictionary::new();
    dictionary.insert(
        PdfName(b"Filter".to_vec()),
        PdfObject::Name(PdfName(b"FlateDecode".to_vec())),
    );
    dictionary.insert(
        PdfName(b"DecodeParms".to_vec()),
        PdfObject::Dictionary(decode_parameters),
    );
    let stream = PdfStream {
        dictionary,
        data: compressed,
    };

    let exact = ParseLimits {
        max_total_decoded_bytes: encoded_predictor_row.len() + 3,
        ..ParseLimits::default()
    };
    assert_eq!(
        decode_stream_with_limits(&stream, &exact).expect("exact predictor boundary"),
        b"abc"
    );
    let short = ParseLimits {
        max_total_decoded_bytes: encoded_predictor_row.len() + 2,
        ..ParseLimits::default()
    };
    let error = decode_stream_with_limits(&stream, &short)
        .expect_err("predictor output allocation must be budgeted");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn wasm_profile_defaults_keep_every_cache_dimension_bounded() {
    let limits = ParseLimits::default();
    assert!(limits.max_cached_object_stream_bytes > 0);
    assert!(limits.max_cached_object_streams > 0);
    assert!(limits.max_cached_object_stream_bytes <= limits.max_total_decoded_bytes);
    assert!(limits.max_decoded_stream_bytes <= limits.max_total_decoded_bytes);
}
