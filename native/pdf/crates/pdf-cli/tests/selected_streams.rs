use std::{path::PathBuf, process::Command};

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/priority")
        .join(name)
}

fn run(arguments: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(arguments)
        .output()
        .unwrap()
}

#[test]
fn selected_layout_ndjson_has_source_ids_and_terminal_patches() {
    let path = fixture("selected-pages.pdf");
    let path = path.to_str().unwrap();
    let output = run(&["layout", path, "--pages", "1,3", "--ndjson"]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let events = String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<serde_json::Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(
        events
            .iter()
            .map(|value| value["event"].as_str().unwrap())
            .collect::<Vec<_>>(),
        ["document_start", "page", "page", "document_finalize"]
    );
    assert_eq!(events[2]["payload"]["page_number"], 3);
    assert_eq!(
        events[3]["payload"]["page_finalizations"][1]["page_index"],
        2
    );
    let incomplete = run(&["layout", path, "--pages", "1-2", "--ndjson"]);
    assert!(!incomplete.status.success());
    let partial = String::from_utf8(incomplete.stdout).unwrap();
    assert_eq!(partial.lines().count(), 2);
    assert!(!partial.contains("document_finalize"));
    assert!(
        serde_json::from_slice::<serde_json::Value>(&incomplete.stderr).unwrap()["error"]["code"]
            .is_string()
    );
}

#[test]
fn selected_text_filters_and_vertical_geometry_are_available() {
    let selected = fixture("selected-pages.pdf");
    let output = run(&[
        "extract",
        selected.to_str().unwrap(),
        "--pages",
        "3",
        "--mode",
        "content-order",
        "--json",
    ]);
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["pages"][0]["page_index"], 2);
    let filters = fixture("filters.pdf");
    let output = run(&["extract", filters.to_str().unwrap(), "--mode", "auto"]);
    assert!(output.status.success());
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        "Filtered text"
    );
    let vertical = fixture("vertical-w2.pdf");
    let output = run(&[
        "extract",
        vertical.to_str().unwrap(),
        "--mode",
        "content-order",
        "--json",
    ]);
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        value["glyphs"][0]["origin"],
        serde_json::json!([47.0, 91.0])
    );
    assert_eq!(
        value["glyphs"][0]["advance"],
        serde_json::json!([0.0, -12.0])
    );
}

#[test]
fn limits_files_reject_unknown_options_and_bound_file_reads() {
    let path = fixture("filters.pdf");
    let config = std::env::temp_dir().join(format!("rust-pdf-limits-{}.json", std::process::id()));
    for (bytes, code) in [
        (br#"{"max_file_bytes":1}"#.as_slice(), "limit_exceeded"),
        (br#"{"typo":1}"#, "invalid_option"),
    ] {
        std::fs::write(&config, bytes).unwrap();
        let output = run(&[
            "--limits",
            config.to_str().unwrap(),
            "inspect",
            path.to_str().unwrap(),
            "--json",
        ]);
        assert!(!output.status.success());
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&output.stderr).unwrap()["error"]["code"],
            code
        );
    }
    std::fs::remove_file(config).unwrap();
}

#[test]
fn ocr_candidates_stream_selected_pages() {
    let path = fixture("ocr-pages.pdf");
    let output = run(&[
        "ocr-candidates",
        path.to_str().unwrap(),
        "--pages",
        "2",
        "--ndjson",
    ]);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["page_index"], 1);
    assert_eq!(value["reason"], "eligible");
}
