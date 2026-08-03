use std::fmt::Write as _;

use pdf_core::{ExtractionMode, PdfDocument, SeparatorOrigin, TextExtractionOptionsV2};

#[test]
fn auto_infers_latin_word_boundary_without_splitting_letters() {
    let mut specs = Vec::new();
    let mut x = 10.0;
    for (index, character) in "ArtificialIntelligence".chars().enumerate() {
        if index == "Artificial".chars().count() {
            x += 3.0;
        }
        specs.push(GlyphSpec::horizontal(character, x, 700.0));
        x += 6.0;
    }
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid Latin PDF");
    let content = extract(&document, ExtractionMode::ContentOrder);
    let legacy = extract(&document, ExtractionMode::Layout);
    let auto = extract(&document, ExtractionMode::Auto);

    assert_eq!(content.text, "ArtificialIntelligence");
    assert!(legacy.text.contains("A r t"));
    assert_eq!(auto.text, "Artificial Intelligence");
    assert_eq!(auto.quality.expect("quality").inserted_spaces, 1);
    assert_eq!(auto.separators.len(), 1);
    assert_eq!(auto.separators[0].origin, SeparatorOrigin::GeometrySpace);
}

#[test]
fn auto_never_inserts_general_gap_spaces_between_cjk_glyphs() {
    let specs = "台灣政府動畫宣導影片"
        .chars()
        .enumerate()
        .map(|(index, character)| {
            let index = u32::try_from(index).expect("fixture index");
            GlyphSpec::horizontal(character, 10.0 + f64::from(index) * 7.0, 700.0)
        })
        .collect::<Vec<_>>();
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid CJK PDF");
    let legacy = extract(&document, ExtractionMode::Layout);
    let auto = extract(&document, ExtractionMode::Auto);

    assert!(legacy.text.contains("台 灣 政 府"));
    assert_eq!(auto.text, "台灣政府動畫宣導影片");
    assert_eq!(auto.quality.expect("quality").inserted_spaces, 0);
}

#[test]
fn auto_canonicalizes_cjk_compatibility_forms_but_preserves_raw_glyphs() {
    let specs = vec![GlyphSpec::horizontal('⽚', 10.0, 700.0)];
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid compatibility PDF");

    let content = extract(&document, ExtractionMode::ContentOrder);
    let auto = extract(&document, ExtractionMode::Auto);

    assert_eq!(content.text, "⽚");
    assert_eq!(auto.text, "片");
    assert_eq!(auto.glyphs[0].unicode, "⽚");
}

#[test]
fn auto_deduplicates_explicit_whitespace_and_traces_line_breaks() {
    let specs = vec![
        GlyphSpec::horizontal('A', 10.0, 700.0),
        GlyphSpec::horizontal(' ', 16.0, 700.0),
        GlyphSpec::horizontal(' ', 22.0, 700.0),
        GlyphSpec::horizontal('B', 28.0, 700.0),
        GlyphSpec::horizontal('C', 10.0, 680.0),
    ];
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid whitespace PDF");
    let auto = extract(&document, ExtractionMode::Auto);

    assert_eq!(auto.text, "A B\nC");
    let quality = auto.quality.expect("quality");
    assert_eq!(quality.inserted_spaces, 0);
    assert_eq!(quality.inserted_line_breaks, 1);
    assert!(
        auto.separators
            .iter()
            .any(|separator| separator.origin == SeparatorOrigin::GeometryLineBreak)
    );
}

#[test]
fn mixed_rotation_is_grouped_deterministically_with_aggregated_warning() {
    let specs = vec![
        GlyphSpec::horizontal('A', 10.0, 700.0),
        GlyphSpec {
            character: '2',
            matrix: [0.0, 1.0, -1.0, 0.0, 20.0, 700.0],
        },
        GlyphSpec {
            character: '0',
            matrix: [0.0, 1.0, -1.0, 0.0, 21.0, 700.0],
        },
        GlyphSpec {
            character: '2',
            matrix: [0.0, 1.0, -1.0, 0.0, 22.0, 700.0],
        },
        GlyphSpec {
            character: '6',
            matrix: [0.0, 1.0, -1.0, 0.0, 23.0, 700.0],
        },
    ];
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid mixed PDF");
    let first = extract(&document, ExtractionMode::Auto);
    let second = extract(&document, ExtractionMode::Auto);

    assert_eq!(first, second);
    assert_eq!(first.text, "A\n2026");
    assert_eq!(
        first
            .warnings
            .iter()
            .filter(|warning| warning.code == "reading_order_ambiguous")
            .count(),
        1
    );
    assert_eq!(first.quality.expect("quality").ambiguous_boundaries, 1);
}

#[test]
fn all_quarter_turn_rotations_are_bucketed_and_warned_once() {
    let specs = vec![
        GlyphSpec {
            character: 'A',
            matrix: [1.0, 0.0, 0.0, 1.0, 10.0, 700.0],
        },
        GlyphSpec {
            character: 'B',
            matrix: [0.0, 1.0, -1.0, 0.0, 20.0, 700.0],
        },
        GlyphSpec {
            character: 'C',
            matrix: [-1.0, 0.0, 0.0, -1.0, 30.0, 700.0],
        },
        GlyphSpec {
            character: 'D',
            matrix: [0.0, -1.0, 1.0, 0.0, 40.0, 700.0],
        },
    ];
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid rotation PDF");
    let first = extract(&document, ExtractionMode::Auto);
    let second = extract(&document, ExtractionMode::Auto);

    assert_eq!(first, second);
    assert_eq!(first.text, "A\nB\nC\nD");
    assert_eq!(
        first
            .glyphs
            .iter()
            .map(|glyph| glyph.rotation_bucket)
            .collect::<Vec<_>>(),
        vec![0, 90, 180, -90]
    );
    assert_eq!(
        first
            .warnings
            .iter()
            .filter(|warning| warning.code == "reading_order_ambiguous")
            .count(),
        1
    );
    assert_eq!(first.quality.expect("quality").ambiguous_boundaries, 1);
}

#[test]
fn extreme_column_gap_falls_back_to_source_order_without_artificial_space() {
    let specs = vec![
        GlyphSpec::horizontal('B', 200.0, 700.0),
        GlyphSpec::horizontal('A', 10.0, 700.0),
    ];
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid columns PDF");
    let auto = extract(&document, ExtractionMode::Auto);

    assert_eq!(auto.text, "BA");
    assert!(auto.separators.is_empty());
    assert_eq!(
        auto.warnings
            .iter()
            .filter(|warning| warning.code == "reading_order_ambiguous")
            .count(),
        1
    );
    assert_eq!(auto.quality.expect("quality").ambiguous_boundaries, 1);
}

#[test]
fn exactly_overlapping_text_is_preserved_conservatively() {
    let specs = vec![
        GlyphSpec::horizontal('A', 10.0, 700.0),
        GlyphSpec::horizontal('A', 10.0, 700.0),
    ];
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid overlap PDF");
    let content = extract(&document, ExtractionMode::ContentOrder);
    let auto = extract(&document, ExtractionMode::Auto);

    assert_eq!(content.text, "AA");
    assert_eq!(auto.text, "AA");
    assert_eq!(auto.glyphs.len(), 2);
    assert!(auto.separators.is_empty());
}

#[test]
fn fixed_large_page_harness_is_deterministic() {
    let specs = (0_u32..2_000)
        .map(|index| {
            GlyphSpec::horizontal(
                'A',
                10.0 + f64::from(index % 100) * 6.0,
                700.0 - f64::from(index / 100) * 14.0,
            )
        })
        .collect::<Vec<_>>();
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid large PDF");
    let first = extract(&document, ExtractionMode::Auto);
    let second = extract(&document, ExtractionMode::Auto);
    assert_eq!(first.text, second.text);
    assert_eq!(first.separators, second.separators);
}

#[test]
#[ignore = "manual Stage 11 release benchmark"]
fn benchmark_legacy_layout_vs_auto_on_fixed_2000_glyph_page() {
    const ITERATIONS: u128 = 50;
    let specs = (0_u32..2_000)
        .map(|index| {
            GlyphSpec::horizontal(
                'A',
                10.0 + f64::from(index % 100) * 6.0,
                700.0 - f64::from(index / 100) * 14.0,
            )
        })
        .collect::<Vec<_>>();
    let document = PdfDocument::parse(&positioned_pdf(&specs)).expect("valid benchmark PDF");

    for mode in [
        ExtractionMode::ContentOrder,
        ExtractionMode::Layout,
        ExtractionMode::Auto,
    ] {
        let warm = extract(&document, mode);
        let expected = warm.text;
        let started = std::time::Instant::now();
        for _ in 0..ITERATIONS {
            let result = extract(&document, mode);
            assert_eq!(result.text, expected);
            std::hint::black_box(result);
        }
        let elapsed = started.elapsed();
        println!(
            "STAGE11_BENCH mode={} iterations={} total_ns={} per_iteration_ns={} text_bytes={}",
            mode.as_str(),
            ITERATIONS,
            elapsed.as_nanos(),
            elapsed.as_nanos() / ITERATIONS,
            expected.len()
        );
    }
}

fn extract(document: &PdfDocument, mode: ExtractionMode) -> pdf_core::ExtractedTextV2 {
    document
        .extract_text_v2(TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode,
            include_quality_metadata: true,
        })
        .expect("text extraction")
}

#[derive(Clone, Copy)]
struct GlyphSpec {
    character: char,
    matrix: [f64; 6],
}

impl GlyphSpec {
    fn horizontal(character: char, x: f64, y: f64) -> Self {
        Self {
            character,
            matrix: [1.0, 0.0, 0.0, 1.0, x, y],
        }
    }
}

fn positioned_pdf(specs: &[GlyphSpec]) -> Vec<u8> {
    let mut content = String::from("BT /F1 12 Tf\n");
    let mut cmap = String::from("1 begincodespacerange <00> <ff> endcodespacerange\n");
    writeln!(cmap, "{} beginbfchar", specs.len()).expect("write CMap");
    for (index, spec) in specs.iter().enumerate() {
        let code = u8::try_from(index % 255 + 1).expect("fixture code");
        let [matrix_a, matrix_b, matrix_c, matrix_d, matrix_e, matrix_f] = spec.matrix;
        writeln!(
            content,
            "{matrix_a} {matrix_b} {matrix_c} {matrix_d} {matrix_e} {matrix_f} Tm <{code:02X}> Tj"
        )
        .expect("write content");
        write!(cmap, "<{code:02X}> <").expect("write mapping");
        for unit in spec.character.encode_utf16(&mut [0_u16; 2]) {
            write!(cmap, "{unit:04X}").expect("write UTF-16");
        }
        cmap.push_str(">\n");
    }
    content.push_str("ET");
    cmap.push_str("endbfchar");

    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(content.as_bytes()),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>".to_vec(),
        stream_body(cmap.as_bytes()),
    ];
    classic_pdf(&objects)
}

fn stream_body(data: &[u8]) -> Vec<u8> {
    let mut body = format!("<< /Length {} >>\nstream\n", data.len()).into_bytes();
    body.extend_from_slice(data);
    body.extend_from_slice(b"\nendstream");
    body
}

fn classic_pdf(objects: &[Vec<u8>]) -> Vec<u8> {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::with_capacity(objects.len());
    for (index, body) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        pdf.extend_from_slice(body);
        pdf.extend_from_slice(b"\nendobj\n");
    }
    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f \n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
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
