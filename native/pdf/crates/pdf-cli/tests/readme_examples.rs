use std::{path::PathBuf, process::Command};

use sha2::{Digest, Sha256};

const MANIFEST: &str = include_str!("../../../tests/fixtures/manifest.toml");
const CLASSIC: &[u8] = include_bytes!("../../../tests/fixtures/valid/classic-minimal.pdf");
const TEXT: &[u8] = include_bytes!("../../../tests/fixtures/valid/text-minimal.pdf");

#[test]
fn public_fixture_manifest_hashes_are_current() {
    for (id, bytes, expected) in [
        (
            "classic-minimal",
            CLASSIC,
            "d92fb245ab1ddc4353b372cb16683f2a340e4a803fb4d7949b03ce8b2122bb31",
        ),
        (
            "text-minimal",
            TEXT,
            "409a5b27adf14ba622a130c73826872155f41c8c3b45176e011a92cbf2056bdb",
        ),
    ] {
        assert_eq!(format!("{:x}", Sha256::digest(bytes)), expected, "{id}");
        assert!(MANIFEST.contains(id), "manifest missing {id}");
        assert!(MANIFEST.contains(expected), "manifest missing {id} hash");
    }
}

#[test]
fn readme_cli_auto_example_extracts_text() {
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/valid/text-minimal.pdf");
    let output = Command::new(env!("CARGO_BIN_EXE_rust-pdf"))
        .args(["extract", fixture.to_str().expect("UTF-8 fixture path")])
        .args(["--mode", "auto", "--json"])
        .output()
        .expect("run README CLI example");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("README JSON output");
    assert_eq!(value["mode"], "auto");
    assert_eq!(value["text"], "Hello PDF text");
    assert_eq!(value["pages"].as_array().expect("pages").len(), 1);
    assert_eq!(value["quality"]["fallback_glyphs"], 14);
}
