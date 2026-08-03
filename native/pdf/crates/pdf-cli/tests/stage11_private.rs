use std::{
    collections::BTreeSet,
    env, fs,
    path::{Path, PathBuf},
    process::{Command, Output},
};

use sha2::{Digest, Sha256};

struct RealWorldCase {
    id: &'static str,
    path_env: &'static str,
    file_name: &'static str,
    sha256: &'static str,
    version: (u64, u64),
    pages: usize,
    in_use_objects: u64,
    layout_utf16_units: usize,
    content_order_utf16_units: usize,
    warning_count: usize,
    warning_codes: &'static [&'static str],
    auto_warning_count: usize,
    auto_warning_codes: &'static [&'static str],
    required_auto: &'static [&'static str],
    forbidden_auto: &'static [&'static str],
}

const CASES: &[RealWorldCase] = &[
    RealWorldCase {
        id: "ai-index-2026",
        path_env: "RUST_PDF_REAL_AI_INDEX",
        file_name: "ai_index_report_2026.pdf",
        sha256: "9e1a0455a77523b9dd86351b13a569b9e4e8173c3031757dff220ad7e238fca5",
        version: (1, 7),
        pages: 423,
        in_use_objects: 45_151,
        layout_utf16_units: 1_090_180,
        content_order_utf16_units: 851_998,
        warning_count: 29,
        warning_codes: &[
            "font_fallback_encoding",
            "unicode_mapping_invalid",
            "unicode_mapping_missing",
        ],
        auto_warning_count: 309,
        auto_warning_codes: &[
            "font_fallback_encoding",
            "reading_order_ambiguous",
            "unicode_mapping_invalid",
            "unicode_mapping_missing",
        ],
        required_auto: &["Artificial Intelligence Index Report 2026"],
        forbidden_auto: &["A r tificial", "Int elligenc e", "Inde x"],
    },
    RealWorldCase {
        id: "taiwan-government-animation",
        path_env: "RUST_PDF_REAL_TAIWAN",
        file_name: "台灣政府動畫宣導影片.pdf",
        sha256: "e197a176b28bc37a43196301a403696ad67c699a69a3f63d0445dda60d9aa597",
        version: (1, 4),
        pages: 15,
        in_use_objects: 1_317,
        layout_utf16_units: 6_176,
        content_order_utf16_units: 4_288,
        warning_count: 0,
        warning_codes: &[],
        auto_warning_count: 15,
        auto_warning_codes: &["reading_order_ambiguous"],
        required_auto: &["台灣政府動畫宣導影片"],
        forbidden_auto: &["台 灣 政 府 動 畫 宣 導"],
    },
];

#[test]
fn private_cli_matrix_if_configured() {
    let corpus_root = env::var_os("RUST_PDF_REAL_CORPUS_DIR").map(PathBuf::from);
    let configured = corpus_root.is_some()
        || CASES
            .iter()
            .any(|case| env::var_os(case.path_env).is_some());
    if !configured {
        eprintln!(
            "SKIP private CLI corpus: set RUST_PDF_REAL_CORPUS_DIR or both per-document path vars"
        );
        return;
    }

    for case in CASES {
        let path = env::var_os(case.path_env)
            .map(PathBuf::from)
            .or_else(|| corpus_root.as_ref().map(|root| root.join(case.file_name)))
            .unwrap_or_else(|| panic!("{} is configured incompletely", case.id));
        verify_case(case, &path);
    }
}

fn verify_case(case: &RealWorldCase, path: &Path) {
    let bytes = fs::read(path)
        .unwrap_or_else(|error| panic!("{} cannot read {}: {error}", case.id, path.display()));
    assert_eq!(format!("{:x}", Sha256::digest(bytes)), case.sha256);

    let inspect = run(path, &["inspect", "--json"]);
    let inspect_json = successful_json(case, "inspect", &inspect);
    assert_eq!(inspect_json["version"]["major"], case.version.0);
    assert_eq!(inspect_json["version"]["minor"], case.version.1);
    assert_eq!(inspect_json["in_use_objects"], case.in_use_objects);

    let validate = run(path, &["validate", "--json", "--diagnostics"]);
    let validate_json = successful_json(case, "validate", &validate);
    assert_eq!(validate_json["ok"], true);
    assert_eq!(validate_json["validated_objects"], case.in_use_objects);
    assert!(validate_json["decode_metrics"].is_object());

    verify_extract(
        case,
        path,
        "content-order",
        Some(case.content_order_utf16_units),
    );
    verify_extract(case, path, "layout", Some(case.layout_utf16_units));
    verify_extract(case, path, "auto", None);
}

fn verify_extract(case: &RealWorldCase, path: &Path, mode: &str, expected_units: Option<usize>) {
    let output = run(path, &["extract", "--mode", mode]);
    assert!(
        output.status.success(),
        "{} {mode}: {}",
        case.id,
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = output.stdout.strip_suffix(b"\n").unwrap_or(&output.stdout);
    let stdout = stdout.strip_suffix(b"\r").unwrap_or(stdout);
    let text = std::str::from_utf8(stdout).expect("CLI text is UTF-8");
    if let Some(expected_units) = expected_units {
        assert_eq!(
            text.encode_utf16().count(),
            expected_units,
            "{} {mode} text baseline changed",
            case.id
        );
    }
    assert_eq!(text.split("\n\n").count(), case.pages);

    let stderr = String::from_utf8(output.stderr).expect("CLI warnings are UTF-8");
    let warning_lines = stderr.lines().collect::<Vec<_>>();
    let (expected_warning_count, expected_warning_codes) = if mode == "auto" {
        (case.auto_warning_count, case.auto_warning_codes)
    } else {
        (case.warning_count, case.warning_codes)
    };
    assert_eq!(
        warning_lines.len(),
        expected_warning_count,
        "{} {mode} warning count changed",
        case.id
    );
    let actual_codes = warning_lines
        .iter()
        .filter_map(|line| {
            line.strip_prefix("warning[")?
                .split_once(']')
                .map(|value| value.0)
        })
        .collect::<BTreeSet<_>>();
    let expected_codes = expected_warning_codes
        .iter()
        .copied()
        .collect::<BTreeSet<_>>();
    assert_eq!(actual_codes, expected_codes);

    if mode == "auto" {
        let normalized = collapse_whitespace(text);
        for fragment in case.required_auto {
            assert!(
                normalized.contains(&collapse_whitespace(fragment)),
                "{} Auto output lacks {fragment:?}",
                case.id
            );
        }
        for fragment in case.forbidden_auto {
            assert!(
                !text.contains(fragment),
                "{} Auto output contains forbidden {fragment:?}",
                case.id
            );
        }
    }
}

fn run(path: &Path, arguments: &[&str]) -> Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_rust-pdf"));
    command.arg(arguments[0]).arg(path).args(&arguments[1..]);
    command.output().expect("run rust-pdf")
}

fn successful_json(case: &RealWorldCase, operation: &str, output: &Output) -> serde_json::Value {
    assert!(
        output.status.success(),
        "{} {operation}: {}",
        case.id,
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("CLI JSON")
}

fn collapse_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}
