use pdf_core::{ErrorCode, ParseLimits, PdfDocument, parse_content};

#[test]
fn every_truncated_prefix_fails_without_panicking() {
    let pdf = include_bytes!("../../../tests/fixtures/valid/classic-minimal.pdf");

    for end in 0..pdf.len() {
        let _ = PdfDocument::parse(&pdf[..end]);
    }

    PdfDocument::parse(pdf).expect("complete fixture remains valid");
}

#[test]
fn content_operation_budget_is_enforced_at_boundary() {
    let limits = ParseLimits {
        max_content_operations: 2,
        ..ParseLimits::default()
    };

    let error = parse_content(b"q Q BT ET", &limits).expect_err("third operation exceeds budget");
    assert_eq!(error.code, ErrorCode::LimitExceeded);
}

#[test]
fn short_malformed_inputs_never_panic() {
    let cases: &[&[u8]] = &[
        b"",
        b"%PDF-",
        b"%PDF-1.7",
        b"%PDF-1.7\nstartxref\n",
        b"%PDF-1.7\nstartxref\n18446744073709551615\n%%EOF",
        b"%PDF-1.7\nxref\n0 1\n",
        b"%PDF-1.7\n1 0 obj\n<< /Length 999999999 >>\nstream\nx",
    ];

    for input in cases {
        let _ = PdfDocument::parse(input);
    }
}
