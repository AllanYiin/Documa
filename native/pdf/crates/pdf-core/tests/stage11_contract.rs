use std::{
    collections::BTreeSet,
    env,
    fmt::Write as _,
    fs,
    path::{Path, PathBuf},
};

use pdf_core::{
    ExtractionMode, ObjectId, PdfDocument, TextExtractionOptions, TextExtractionOptionsV2, XrefKind,
};
use sha2::{Digest, Sha256};

const REAL_WORLD_MANIFEST: &str = include_str!("../../../tests/real-world/manifest.toml.example");
const BASELINE_GOLDEN: &str = include_str!("../../../tests/fixtures/stage11/baseline-golden.toml");

const LATIN_TEXT: &str = "Artificial Intelligence Index Report 2026";
const LATIN_LAYOUT_BASELINE: &str =
    "A r t i f i c i a l I n t e l l i g e n c e I n d e x R e p o r t 2 0 2 6";
const CJK_TEXT: &str = "台灣政府動畫宣導影片";
const CJK_LAYOUT_BASELINE: &str = "台 灣 政 府 動 畫 宣 導 影 片";

struct RealWorldCase {
    id: &'static str,
    file_name: &'static str,
    path_env: &'static str,
    sha256: &'static str,
    version: (u8, u8),
    pages: usize,
    in_use_objects: usize,
    layout_utf16_units: usize,
    content_order_utf16_units: usize,
    warning_count: usize,
    actual_text_replacements: usize,
    layout_fragment: &'static str,
    content_order_fragment: &'static str,
    warning_codes: &'static [&'static str],
    target_auto_fragments: &'static [&'static str],
    forbidden_auto_fragments: &'static [&'static str],
}

const REAL_WORLD_CASES: &[RealWorldCase] = &[
    RealWorldCase {
        id: "ai-index-2026",
        file_name: "ai_index_report_2026.pdf",
        path_env: "RUST_PDF_REAL_AI_INDEX",
        sha256: "9e1a0455a77523b9dd86351b13a569b9e4e8173c3031757dff220ad7e238fca5",
        version: (1, 7),
        pages: 423,
        in_use_objects: 45_151,
        layout_utf16_units: 1_090_180,
        content_order_utf16_units: 851_998,
        warning_count: 29,
        actual_text_replacements: 311,
        layout_fragment: "A r tificial",
        content_order_fragment: "Artificial IntelligenceIndex Report2026",
        warning_codes: &[
            "font_fallback_encoding",
            "unicode_mapping_invalid",
            "unicode_mapping_missing",
        ],
        target_auto_fragments: &["Artificial Intelligence Index Report 2026"],
        forbidden_auto_fragments: &["A r tificial", "Int elligenc e", "Inde x"],
    },
    RealWorldCase {
        id: "taiwan-government-animation",
        file_name: "台灣政府動畫宣導影片.pdf",
        path_env: "RUST_PDF_REAL_TAIWAN",
        sha256: "e197a176b28bc37a43196301a403696ad67c699a69a3f63d0445dda60d9aa597",
        version: (1, 4),
        pages: 15,
        in_use_objects: 1_317,
        layout_utf16_units: 6_176,
        content_order_utf16_units: 4_288,
        warning_count: 0,
        actual_text_replacements: 248,
        layout_fragment: "台 灣 政 府 動 畫 宣 導",
        content_order_fragment: "台灣政府動畫宣導影片",
        warning_codes: &[],
        target_auto_fragments: &["台灣政府動畫宣導影片"],
        forbidden_auto_fragments: &["台 灣 政 府 動 畫 宣 導"],
    },
];

#[test]
fn corpus_contract_is_versioned_and_matches_runner_constants() {
    assert!(REAL_WORLD_MANIFEST.contains("schema_version = 1"));
    assert!(BASELINE_GOLDEN.contains("schema_version = 1"));
    for case in REAL_WORLD_CASES {
        assert!(REAL_WORLD_MANIFEST.contains(case.id));
        assert!(REAL_WORLD_MANIFEST.contains(case.file_name));
        assert!(REAL_WORLD_MANIFEST.contains(case.path_env));
        assert!(REAL_WORLD_MANIFEST.contains(case.sha256));
    }

    let regression_source = include_str!("real_world_regressions.rs");
    assert!(regression_source.contains("highly_compressible_xref_stream_uses_structural_budget"));
    assert!(regression_source.contains("df0fd835"));
    let stage3_source = include_str!("stage3.rs");
    assert!(stage3_source.contains("highly_compressible_object_stream_uses_structural_budget"));
}

#[test]
fn corpus_hash_mismatch_is_a_hard_failure() {
    let error = verify_sha256("tampered-fixture", b"tampered", &"0".repeat(64))
        .expect_err("wrong digest must fail before parsing");
    assert!(error.contains("tampered-fixture SHA-256 mismatch"));
}

#[test]
fn generated_latin_fixture_records_current_layout_artifact() {
    let pdf = positioned_text_pdf(LATIN_TEXT);
    let document = PdfDocument::parse(&pdf).expect("generated Latin fixture");

    let content_order = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: false,
        })
        .expect("content-order extraction");
    let layout = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: true,
        })
        .expect("layout extraction");

    assert_eq!(content_order.text, LATIN_TEXT);
    assert_eq!(layout.text, LATIN_LAYOUT_BASELINE);
    assert!(content_order.warnings.is_empty());
    assert!(layout.warnings.is_empty());
    assert!(BASELINE_GOLDEN.contains(LATIN_TEXT));
    assert!(BASELINE_GOLDEN.contains(LATIN_LAYOUT_BASELINE));
}

#[test]
fn generated_cjk_fixture_records_current_layout_artifact() {
    let pdf = positioned_text_pdf(CJK_TEXT);
    let document = PdfDocument::parse(&pdf).expect("generated CJK fixture");

    let content_order = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: false,
        })
        .expect("content-order extraction");
    let layout = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: true,
        })
        .expect("layout extraction");

    assert_eq!(content_order.text, CJK_TEXT);
    assert_eq!(layout.text, CJK_LAYOUT_BASELINE);
    assert!(content_order.warnings.is_empty());
    assert!(layout.warnings.is_empty());
    assert!(BASELINE_GOLDEN.contains(CJK_TEXT));
    assert!(BASELINE_GOLDEN.contains(CJK_LAYOUT_BASELINE));
}

#[test]
fn private_real_world_contract_if_configured() {
    let corpus_root = env::var_os("RUST_PDF_REAL_CORPUS_DIR").map(PathBuf::from);
    let configured = corpus_root.is_some()
        || REAL_WORLD_CASES
            .iter()
            .any(|case| env::var_os(case.path_env).is_some());
    if !configured {
        eprintln!(
            "SKIP private real-world corpus: set RUST_PDF_REAL_CORPUS_DIR or per-document path vars"
        );
        return;
    }

    for case in REAL_WORLD_CASES {
        let path = configured_path(case, corpus_root.as_deref())
            .unwrap_or_else(|| panic!("{} is configured incompletely", case.id));
        verify_real_world_case(case, &path);
    }
}

fn configured_path(case: &RealWorldCase, corpus_root: Option<&Path>) -> Option<PathBuf> {
    env::var_os(case.path_env)
        .map(PathBuf::from)
        .or_else(|| corpus_root.map(|root| root.join(case.file_name)))
}

fn verify_real_world_case(case: &RealWorldCase, path: &Path) {
    let bytes = fs::read(path).unwrap_or_else(|error| {
        panic!("failed to read {} at {}: {error}", case.id, path.display())
    });
    verify_sha256(case.id, &bytes, case.sha256).unwrap_or_else(|error| panic!("{error}"));

    let document =
        PdfDocument::parse(&bytes).unwrap_or_else(|error| panic!("{} parse: {error}", case.id));
    let summary = document
        .summary()
        .unwrap_or_else(|error| panic!("{} summary: {error}", case.id));
    assert_eq!((summary.version.major, summary.version.minor), case.version);
    assert_eq!(summary.in_use_objects, case.in_use_objects);

    let mut validated = 0_usize;
    for (&number, entry) in document.xref_entries() {
        if entry.kind != XrefKind::Free {
            document
                .object(ObjectId::new(number, entry.generation))
                .unwrap_or_else(|error| panic!("{} object {number}: {error}", case.id));
            validated += 1;
        }
    }
    assert_eq!(validated, case.in_use_objects);

    let layout = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: true,
        })
        .unwrap_or_else(|error| panic!("{} layout extraction: {error}", case.id));
    let content_order = document
        .extract_text(TextExtractionOptions {
            normalize_unicode: false,
            layout: false,
        })
        .unwrap_or_else(|error| panic!("{} content-order extraction: {error}", case.id));
    let auto = document
        .extract_text_v2(TextExtractionOptionsV2 {
            normalize_unicode: false,
            mode: ExtractionMode::Auto,
            include_quality_metadata: true,
        })
        .unwrap_or_else(|error| panic!("{} auto extraction: {error}", case.id));

    assert_eq!(layout.pages.len(), case.pages);
    assert_eq!(content_order.pages.len(), case.pages);
    assert_eq!(auto.pages.len(), case.pages);
    assert_eq!(
        auto.glyphs
            .iter()
            .filter(|glyph| glyph.text_origin == pdf_core::TextOrigin::ActualText)
            .count(),
        case.actual_text_replacements,
        "{} ActualText replacement count changed",
        case.id
    );
    assert_eq!(
        layout.text.encode_utf16().count(),
        case.layout_utf16_units,
        "{} layout baseline changed",
        case.id
    );
    assert_eq!(
        content_order.text.encode_utf16().count(),
        case.content_order_utf16_units,
        "{} content-order baseline changed",
        case.id
    );
    assert!(layout.text.contains(case.layout_fragment));
    assert!(content_order.text.contains(case.content_order_fragment));
    assert_eq!(layout.warnings.len(), case.warning_count);
    assert_eq!(content_order.warnings.len(), case.warning_count);

    let actual_codes = layout
        .warnings
        .iter()
        .map(|warning| warning.code.as_str())
        .collect::<BTreeSet<_>>();
    let expected_codes = case.warning_codes.iter().copied().collect::<BTreeSet<_>>();
    assert_eq!(actual_codes, expected_codes);

    let normalized_auto_text = collapse_whitespace(&auto.text);
    for fragment in case.target_auto_fragments {
        assert!(
            normalized_auto_text.contains(&collapse_whitespace(fragment)),
            "{} auto output is missing required fragment {fragment:?}",
            case.id
        );
    }
    for fragment in case.forbidden_auto_fragments {
        assert!(
            !auto.text.contains(fragment),
            "{} auto output contains forbidden fragment {fragment:?}",
            case.id
        );
    }
}

fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn verify_sha256(id: &str, bytes: &[u8], expected: &str) -> Result<(), String> {
    let actual = format!("{:x}", Sha256::digest(bytes));
    if actual == expected {
        Ok(())
    } else {
        Err(format!(
            "{id} SHA-256 mismatch: expected {expected}, got {actual}"
        ))
    }
}

fn positioned_text_pdf(text: &str) -> Vec<u8> {
    let mut codes = Vec::new();
    let mut cjk_mappings = Vec::new();
    for character in text.chars() {
        if character.is_ascii() {
            codes.push(u8::try_from(u32::from(character)).expect("ASCII fixture code"));
        } else {
            let existing = cjk_mappings
                .iter()
                .position(|(_, mapped)| *mapped == character);
            let index = existing.unwrap_or_else(|| {
                let index = cjk_mappings.len();
                let code = 0x81_u8
                    .checked_add(u8::try_from(index).expect("fixture mapping count"))
                    .expect("fixture code range");
                cjk_mappings.push((code, character));
                index
            });
            codes.push(cjk_mappings[index].0);
        }
    }

    let mut content = b"BT /F1 12 Tf\n".to_vec();
    for (index, code) in codes.iter().enumerate() {
        let x = 10 + index * 6;
        content.extend_from_slice(format!("1 0 0 1 {x} 700 Tm <{code:02X}> Tj\n").as_bytes());
    }
    content.extend_from_slice(b"ET");

    let cmap = fixture_cmap(&cjk_mappings);
    let objects = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] \
          /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
            .to_vec(),
        stream_body(&content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>".to_vec(),
        stream_body(cmap.as_bytes()),
    ];
    classic_pdf(&objects)
}

fn fixture_cmap(cjk_mappings: &[(u8, char)]) -> String {
    let mut cmap = String::from(
        "/CIDInit /ProcSet findresource begin\n\
         12 dict begin\n\
         begincmap\n\
         /CIDSystemInfo << /Registry (Project) /Ordering (Unicode) /Supplement 0 >> def\n\
         /CMapName /Project-Stage11 def\n\
         /CMapType 2 def\n\
         1 begincodespacerange <00> <ff> endcodespacerange\n\
         1 beginbfrange <20> <7e> <0020> endbfrange\n",
    );
    if !cjk_mappings.is_empty() {
        writeln!(cmap, "{} beginbfchar", cjk_mappings.len()).expect("write CMap");
        for (code, character) in cjk_mappings {
            let encoded = character.encode_utf16(&mut [0_u16; 2]).to_vec();
            write!(cmap, "<{code:02X}> <").expect("write CMap");
            for unit in encoded {
                write!(cmap, "{unit:04X}").expect("write CMap");
            }
            cmap.push_str(">\n");
        }
        cmap.push_str("endbfchar\n");
    }
    cmap.push_str(
        "endcmap\n\
         CMapName currentdict /CMap defineresource pop\n\
         end\n\
         end",
    );
    cmap
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
