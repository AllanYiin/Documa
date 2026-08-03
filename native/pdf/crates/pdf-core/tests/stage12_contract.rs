use serde_json::Value;

const CONTRACT: &str = include_str!("../../../tests/fixtures/stage12/baseline-contract.json");
const STAGE_README: &str = include_str!("../../../docs/specs/stage-12/README.md");
const TECHNICAL_SPEC: &str = include_str!("../../../docs/specs/stage-12/technical-spec.md");
const COORDINATE_SPEC: &str = include_str!("../../../docs/specs/stage-12/coordinate-system.md");
const BASELINE_RUNNER: &str = include_str!("../../../tools/stage12_baseline.py");
const COORDINATE_PARITY_RUNNER: &str = include_str!("../../../tools/stage12_coordinate_parity.py");
const LAYOUT_BENCHMARK_RUNNER: &str = include_str!("../../../tools/stage12_layout_benchmark.py");
const LAYOUT_IR_SPEC: &str = include_str!("../../../docs/specs/stage-12/layout-ir-schema.md");
const LAYOUT_IR_SOURCE: &str = include_str!("../src/layout_ir.rs");
const TEXT_SOURCE: &str = include_str!("../src/text.rs");
const FONT_METRICS_SOURCE: &str = include_str!("../src/font_metrics.rs");
const STAGE12_LAYOUT_IR_TESTS: &str = include_str!("stage12_layout_ir.rs");
const CLI_SOURCE: &str = include_str!("../../pdf-cli/src/main.rs");
const PYTHON_NATIVE_SOURCE: &str = include_str!("../../../bindings/python/src/lib.rs");
const PYTHON_API_SOURCE: &str =
    include_str!("../../../bindings/python/python/rust_pdf/__init__.py");
const WASM_SOURCE: &str = include_str!("../../../bindings/wasm/src/lib.rs");
const STAGE1B_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage1b-dod.md");
const TAGGED_SPEC: &str = include_str!("../../../docs/specs/stage-12/tagged-structure.md");
const TAGGED_RUNNER: &str = include_str!("../../../tools/stage12_tagged_benchmark.py");
const TAGGED_SOURCE: &str = include_str!("../src/tagged_structure.rs");
const MARKED_SOURCE: &str = include_str!("../src/marked_content.rs");
const STAGE2_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage2-dod.md");
const READING_ORDER_SPEC: &str = include_str!("../../../docs/specs/stage-12/reading-order.md");
const READING_ORDER_SOURCE: &str = include_str!("../src/reading_order.rs");
const READING_ORDER_RUNNER: &str =
    include_str!("../../../tools/stage12_reading_order_benchmark.py");
const STAGE3_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage3-dod.md");
const TABLE_SPEC: &str = include_str!("../../../docs/specs/stage-12/table-reconstruction.md");
const TABLE_SOURCE: &str = include_str!("../src/table_reconstruction.rs");
const VECTOR_SOURCE: &str = include_str!("../src/vector_paths.rs");
const TABLE_TESTS: &str = include_str!("stage12_table_reconstruction.rs");
const TABLE_BENCHMARK: &str = include_str!("../../../tools/stage12_table_benchmark.py");
const STAGE4_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage4-dod.md");
const CLI_LAYOUT_TESTS: &str = include_str!("../../pdf-cli/tests/layout.rs");
const PYTHON_LAYOUT_TESTS: &str = include_str!("../../../bindings/python/tests/test_stage11.py");
const WASM_LAYOUT_TESTS: &str = include_str!("../../../bindings/wasm/tests/stage11_web.rs");
const IMAGE_NAVIGATION_SPEC: &str =
    include_str!("../../../docs/specs/stage-12/image-placement-navigation.md");
const IMAGE_PLACEMENT_TESTS: &str = include_str!("stage12_image_placements.rs");
const FIGURE_FLOW_SOURCE: &str = include_str!("../src/figure_flow.rs");
const FIGURE_FLOW_TESTS: &str = include_str!("stage12_figure_flow.rs");
const STAGE5A_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage5a-dod.md");
const NAVIGATION_SOURCE: &str = include_str!("../src/navigation.rs");
const NAVIGATION_TESTS: &str = include_str!("stage12_navigation.rs");
const STAGE5B_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage5b-dod.md");
const STAGE5_BENCHMARK: &str = include_str!("../../../tools/stage12_image_navigation_benchmark.py");
const STAGE5_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage5-dod.md");
const STAGE6_SPEC: &str = include_str!("../../../docs/specs/stage-12/documa-shadow-adapter.md");
const STAGE6_NATIVE_SPEC: &str =
    include_str!("../../../docs/specs/stage-12/native-page-production.md");
const STAGE6_BENCHMARK: &str = include_str!("../../../tools/stage12_documa_shadow.py");
const STAGE6AB_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6ab-dod.md");
const LAYOUT_EVENT_TESTS: &str = include_str!("stage12_layout_events.rs");
const STAGE6C2A_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6c2a-dod.md");
const STAGE6C2B_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6c2b-dod.md");
const STAGE6C2C_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6c2c-dod.md");
const STAGE6C2D_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6c2d-dod.md");
const STAGE6C2E_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6c2e-dod.md");
const STAGE6D_PROFILE: &str = include_str!("../../../tools/stage12_documa_metadata_profile.py");
const STAGE6D_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage6d-dod.md");
const STAGE7_TECHNICAL: &str =
    include_str!("../../../docs/specs/stage-12/quality-recovery-technical.md");
const STAGE7_NONTECHNICAL: &str =
    include_str!("../../../docs/specs/stage-12/quality-recovery-nontechnical.md");
const STAGE7_AGENT_PLAN: &str =
    include_str!("../../../docs/specs/stage-12/quality-recovery-agent-plan.md");
const STAGE7_0_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage7-0-dod.md");
const STAGE7A_RUNNER: &str = include_str!("../../../tools/stage12_page_quality_diff.py");
const STAGE7A_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage7a-dod.md");
const STAGE7B_RUNNER: &str = include_str!("../../../tools/stage12_parser_text_quality.py");
const STAGE7B_SPEC: &str = include_str!("../../../docs/specs/stage-12/parser-text-quality.md");
const STAGE7B_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage7b-dod.md");
const STAGE7C_RUNNER: &str = include_str!("../../../tools/stage12_order_gold.py");
const STAGE7C_GOLD: &str =
    include_str!("../../../tests/fixtures/stage12/quality/order/public-gold.json");
const STAGE7C_PRIVATE: &str = include_str!(
    "../../../tests/fixtures/stage12/quality/order/private-order-manifest.example.json",
);
const STAGE7C_REVIEW: &str = include_str!("../../../docs/specs/stage-12/order-gold-review.md");
const STAGE7C_PACKET: &str = include_str!("../../../tools/stage12_order_review_packet.py");
const STAGE7C_UI: &str = include_str!("../../../tools/stage12_order_review_ui.html");
const STAGE7C_UI_SPEC: &str = include_str!("../../../docs/specs/stage-12/order-review-ui.md");
const STAGE7C_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage7c-dod.md");
const STAGE7C_BBOX_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage7c-bbox-dod.md");
const STAGE7C_BLOCK_DOD: &str =
    include_str!("../../../tests/fixtures/stage12/stage7c-block-dod.md");
const STAGE7C_BRUSH_DOD: &str =
    include_str!("../../../tests/fixtures/stage12/stage7c-brush-dod.md");
const STAGE73D_PILOT: &str = include_str!("../../../tools/stage12_order_pilot.py");
const STAGE73D_SPEC: &str = include_str!("../../../docs/specs/stage-12/order-review-pilot.md");
const STAGE73D_EXAMPLE: &str =
    include_str!("../../../tests/fixtures/stage12/quality/order/private-pilot.example.json",);
const STAGE73D_DOD: &str = include_str!("../../../tests/fixtures/stage12/stage7c-pilot-dod.md");

#[test]
fn baseline_contract_is_versioned_complete_and_private() {
    let contract: Value = serde_json::from_str(CONTRACT).expect("valid Stage 12 contract JSON");
    assert_eq!(contract["schema_version"], 1);
    assert_eq!(contract["private_corpus"], true);
    assert_eq!(contract["redistributable"], false);
    assert_eq!(contract["root_env"], "RUST_PDF_STAGE12_CORPUS_DIR");
    assert_eq!(contract["measurement"]["save_private_ir_by_default"], false);

    let documents = contract["documents"].as_array().expect("documents array");
    assert_eq!(documents.len(), 7);
    let total_pages = documents
        .iter()
        .map(|document| {
            assert_eq!(document["sha256"].as_str().expect("sha256").len(), 64);
            assert!(document["bytes"].as_u64().expect("bytes") > 0);
            document["pages"].as_u64().expect("pages")
        })
        .sum::<u64>();
    assert_eq!(total_pages, 1_113);
}

#[test]
fn coordinate_contract_forbids_implicit_space_mixing() {
    for required in [
        "layout_unrotated_top_left",
        "PdfUserSpace",
        "LayoutSpace",
        "DisplaySpace",
        "pdf_to_layout",
        "layout_to_pdf",
        "layout_to_display",
        "display_to_layout",
        "UserUnit",
        "CropBox",
        "BBox",
        "Quad",
        "1e-6 pt",
        "0.5 pt",
    ] {
        assert!(COORDINATE_SPEC.contains(required), "missing {required}");
    }
    assert!(COORDINATE_SPEC.contains("Never mix page dimensions"));
}

#[test]
fn stage_zero_deliverables_and_privacy_defaults_are_executable() {
    assert!(STAGE_README.contains("Stage 0"));
    assert!(TECHNICAL_SPEC.contains("Go/No-Go"));
    assert!(TECHNICAL_SPEC.contains("PyMuPDF raw"));
    assert!(TECHNICAL_SPEC.contains("Documa"));
    assert!(BASELINE_RUNNER.contains("--write-private-ir"));
    assert!(BASELINE_RUNNER.contains("save_private_ir_by_default"));
    assert!(BASELINE_RUNNER.contains("SHA-256 mismatch"));
    assert!(BASELINE_RUNNER.contains("quality_proxy_rust_vs_documa"));
}

#[test]
fn stage_one_coordinate_parity_contract_is_executable_and_private() {
    assert!(COORDINATE_PARITY_RUNNER.contains("layout_unrotated_top_left"));
    assert!(COORDINATE_PARITY_RUNNER.contains("TOLERANCE_PT = 0.5"));
    assert!(COORDINATE_PARITY_RUNNER.contains("\"contains_extracted_content\": False"));
    assert!(COORDINATE_PARITY_RUNNER.contains("\"mismatch_count\""));
}

#[test]
fn stage_one_b_layout_ir_is_versioned_and_cross_frontend() {
    for required in [
        "DocumentLayout",
        "PageLayout",
        "source_order",
        "tagged_order",
        "inferred_order",
        "main_flow",
        "layout_unrotated_top_left",
        "Debug glyphs are also opt-in",
    ] {
        assert!(LAYOUT_IR_SPEC.contains(required), "missing {required}");
    }
    assert!(LAYOUT_IR_SOURCE.contains("pub const LAYOUT_IR_SCHEMA_VERSION: u32 = 1"));
    assert!(LAYOUT_IR_SOURCE.contains("pub fn extract_layout"));
    assert!(LAYOUT_IR_SOURCE.contains("layout_text_bbox_estimated"));
    assert!(CLI_SOURCE.contains("Command::Layout"));
    assert!(PYTHON_NATIVE_SOURCE.contains("fn extract_layout_json"));
    assert!(PYTHON_API_SOURCE.contains("def extract_layout("));
    assert!(WASM_SOURCE.contains("js_name = extractLayout"));
    assert!(WASM_SOURCE.contains("pub fn extract_layout"));
}

#[test]
fn stage_one_b_private_benchmark_audits_schema_without_persisting_ir() {
    for required in [
        "audit_layout",
        "assert_privacy_safe",
        "private_ir_written\": False",
        "contains_extracted_content\": False",
        "all_schema_audits_passed",
        "speedup_vs_frozen_stage0_documa",
        "non_simultaneous_comparison_to_frozen_stage0_baseline",
    ] {
        assert!(
            LAYOUT_BENCHMARK_RUNNER.contains(required),
            "missing {required}"
        );
    }
    assert!(!LAYOUT_BENCHMARK_RUNNER.contains("write_private_ir"));
}

#[test]
fn stage_one_b_completion_evidence_keeps_cutover_closed() {
    for required in [
        "Status: PASS",
        "274.353900 pages/s",
        "37.752772x",
        "271,882,351",
        "00f244b330419058e13dd019a6e9c88aeebfab58bd084bc5f7c75b5e29a49345",
        "Stage 1B is complete",
        "Default-provider cutover remains",
    ] {
        assert!(STAGE1B_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_two_tagged_contract_is_bounded_and_core_owned() {
    for required in [
        "StructTreeRoot",
        "RoleMap",
        "ParentTree",
        "tagged_order",
        "source_order",
        "Alt does not replace text",
        "Artifact content is preserved",
        "max_object_depth",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(TAGGED_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "walk_kid",
        "walk_number_tree",
        "resolve_parent_array",
        "max_structure_elements",
        "max_structure_kids",
        "max_parent_tree_entries",
        "max_role_map_entries",
        "tagged_structure_cycle",
        "parent_tree_mismatch",
    ] {
        assert!(TAGGED_SOURCE.contains(required), "missing {required}");
    }
    assert!(MARKED_SOURCE.contains("alt_text"));
    assert!(MARKED_SOURCE.contains("artifact"));
    assert!(LAYOUT_IR_SOURCE.contains("mcid_node_indices"));
    assert!(LAYOUT_IR_SOURCE.contains("TAGGED_BLOCK_RULE_ID"));
}

#[test]
fn stage_two_private_benchmark_is_deterministic_and_privacy_safe() {
    for required in [
        "audit_stage2_layout",
        "assert_privacy_safe",
        "private_ir_written\": False",
        "contains_extracted_content\": False",
        "tagged_pages",
        "associated_mcids",
        "artifact",
        "warning_code_counts",
        "all_schema_audits_passed",
    ] {
        assert!(TAGGED_RUNNER.contains(required), "missing {required}");
    }
    assert!(!TAGGED_RUNNER.contains("write_private_ir"));
}

#[test]
fn stage_two_completion_evidence_keeps_cutover_closed() {
    for required in [
        "Status: PASS",
        "218.501999 pages/s",
        "30.067210x",
        "289,055,422",
        "919b5be5995433aa9b3e970303255ebec9fdacad1278e459c8e3718070f41ff9",
        "13 focused tagged-structure tests pass",
        "Stage 2 is complete",
        "Default-provider cutover",
    ] {
        assert!(STAGE2_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_three_reading_order_contract_is_bounded_and_core_owned() {
    for required in [
        "layout_unrotated_top_left",
        "source_order",
        "tagged_order",
        "inferred_order",
        "main_flow",
        "visual_reading",
        "simultaneous",
        "focus_candidates",
        "skip_ahead",
        "regression",
        "not calibrated gaze probabilities",
        "Stage 7.4",
        "XY-cut",
        "list-marker",
        "Header",
        "Footer",
        "PageNumber",
        "max_text_spans",
        "O(n log n)",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(READING_ORDER_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "xy_order",
        "line_starts_list_marker",
        "canonical_roman",
        "page_number_like",
        "reading_order_ambiguous",
        "page_furniture_ambiguous",
        "build_visual_reading",
        "MAX_FOCUS_CANDIDATES",
        "saturating_mul(3)",
        "max_text_spans",
        "excluded_ids",
    ] {
        assert!(
            READING_ORDER_SOURCE.contains(required),
            "missing {required}"
        );
    }
}

#[test]
fn stage_three_private_benchmark_is_deterministic_and_privacy_safe() {
    for required in [
        "audit_stage3_layout",
        "assert_privacy_safe",
        "private_ir_written\": False",
        "contains_extracted_content\": False",
        "tagged_pairwise_accuracy",
        "main_flow_coverage",
        "multi_column_proxy_pages",
        "warning_code_counts",
        "all_schema_audits_passed",
    ] {
        assert!(
            READING_ORDER_RUNNER.contains(required),
            "missing {required}"
        );
    }
    assert!(!READING_ORDER_RUNNER.contains("write_private_ir"));
}

#[test]
fn stage_three_completion_evidence_keeps_cutover_closed() {
    for required in [
        "Status: PASS",
        "208.084305 pages/s",
        "28.633671x",
        "330,906,731",
        "0.940546",
        "be92154b73b87f7de9b803c1ed2375a33b143c5b33db9108ebe130cd4693f6c6",
        "14/14",
        "Stage 3 is complete",
        "cutover remains forbidden",
    ] {
        assert!(STAGE3_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_four_table_contract_fixes_coordinates_precedence_and_bounds() {
    for required in [
        "layout_unrotated_top_left",
        "x_direction = right",
        "y_direction = down",
        "pdf_to_layout",
        "Table -> TR -> TH | TD",
        "RowSpan",
        "ColSpan",
        "vector lattice",
        "borderless text alignment",
        "table_detection_ambiguous",
        "max_table_cells",
        "TEDS-S >= 0.90",
        "Default-provider cutover remains closed",
    ] {
        assert!(TABLE_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "apply_tagged_tables",
        "place_tagged_cells",
        "LayoutTableEvidence::Tagged",
        "table_cell_unassigned",
        "max_table_cells",
        "source_node_ids",
        "apply_vector_tables",
        "apply_text_tables",
        "text_run_is_table",
        "numeric_like",
        "fuse_tagged_vector_table",
        "LayoutTableEvidence::Fused",
        "table_evidence_conflict",
        "stage4d_tagged_vector_fusion_v1",
    ] {
        assert!(TABLE_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "collect_vector_paths",
        "process_form",
        "pdf_point_to_layout",
        "max_path_segments",
        "vector graphics-state depth limit exceeded",
    ] {
        assert!(VECTOR_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "tagged_table_preserves_spans_headers_text_geometry_and_node_links",
        "role_map_aliases_and_empty_tagged_cells",
        "invalid_tagged_span_preserves_text",
        "tagged_table_limits_have_exact_and_one_short_boundaries",
        "ruled_table_uses_layout_space",
        "form_matrix_and_page_projection_are_applied_exactly_once",
        "borderless_three_column_table_uses_stable_text_alignment",
        "key_value_and_prose_are_rejected",
        "text_alignment_limits_have_exact_and_one_short_boundaries",
        "cjk_multiline_mixed_sizes_and_rotated_metadata_keep_layout_space_cells",
        "matching_tagged_and_vector_evidence_fuses_without_losing_author_semantics",
        "conflicting_vector_topology_preserves_tagged_table_and_warns_once",
    ] {
        assert!(TABLE_TESTS.contains(required), "missing {required}");
    }
    for (source, required) in [
        (
            CLI_LAYOUT_TESTS,
            "layout_command_exposes_stage4_table_schema",
        ),
        (PYTHON_LAYOUT_TESTS, "test_stage12_table_schema_is_exposed"),
        (WASM_LAYOUT_TESTS, "stage12_table_schema_is_exposed"),
    ] {
        assert!(source.contains(required), "missing {required}");
    }
    for required in [
        "205.731635 pages/s",
        "28.309930x",
        "private_teds_s",
        "2a49875c7da1bf81145e27cad299f4b24a9c97ab4ef46e2193efe55d12e73de8",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE4_DOD.contains(required), "missing {required}");
    }
    for required in [
        "stage4_table_reconstruction",
        "audit_table",
        "run_layout_measured",
        "table_evidence_counts",
        "max_peak_rss_bytes",
        "throughput_ratio_vs_stage3",
        "private_teds_s",
        "assert_privacy_safe",
    ] {
        assert!(TABLE_BENCHMARK.contains(required), "missing {required}");
    }
}
#[test]
fn stage_five_a_image_contract_fixes_occurrences_coordinates_and_recovery() {
    for required in [
        "layout_unrotated_top_left",
        "top_left     = (0, 1)",
        "top_right    = (1, 1)",
        "bottom_right = (1, 0)",
        "bottom_left  = (0, 0)",
        "pdf_to_layout",
        "Page Rotate remains",
        "unapplied",
        "max_images",
        "image_placement_invalid",
        "A limit breach is fatal `limit_exceeded`",
    ] {
        assert!(
            IMAGE_NAVIGATION_SPEC.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "LayoutImagePlacement",
        "append_image_placement",
        "next_image_paint_ordinal",
        "stage5a_image_do_v1",
        "pdf_point_to_layout",
        "image placement count limit exceeded",
        "image_placement_error",
    ] {
        assert!(VECTOR_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "repeated_image_do_occurrences_preserve_layout_quad_bbox_object_and_ordinals",
        "form_matrix_places_nested_image_and_preserves_resource_path",
        "crop_box_user_unit_and_page_rotation_follow_layout_space_once",
        "malformed_optional_image_placement_warns_and_preserves_text_and_later_occurrence",
        "image_occurrence_limit_has_exact_and_one_short_boundaries",
    ] {
        assert!(
            IMAGE_PLACEMENT_TESTS.contains(required),
            "missing {required}"
        );
    }
    for (source, required) in [
        (
            CLI_LAYOUT_TESTS,
            "layout_command_exposes_stage5_image_placement_schema",
        ),
        (
            PYTHON_LAYOUT_TESTS,
            "test_stage12_image_placement_schema_is_exposed",
        ),
        (
            WASM_LAYOUT_TESTS,
            "stage12_image_placement_schema_is_exposed",
        ),
    ] {
        assert!(source.contains(required), "missing {required}");
    }
    assert!(LAYOUT_IR_SOURCE.contains("image_placements: true"));
    assert!(LAYOUT_IR_SOURCE.contains("image_placement_invalid"));
}
#[test]
fn stage_five_b_figure_caption_contract_is_conservative_and_core_owned() {
    for required in [
        "tagged Figure association has precedence",
        "outside a table",
        "not furniture/artifact",
        "figure_caption_ambiguous",
        "image_placement_unassigned",
        "never deletes or",
        "rewrites semantic nodes",
    ] {
        assert!(
            IMAGE_NAVIGATION_SPEC.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "resolve_marked_content_properties",
        "push_marked_content",
        "properties.mcid",
        "properties.artifact",
        "properties.alt_text",
    ] {
        assert!(VECTOR_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "apply_figure_caption_flow",
        "caption_node_eligible",
        "MAX_CAPTION_GAP_PT",
        "figure_caption_ambiguous",
        "image_placement_unassigned",
        "stage5b_tagged_figure_caption_v1",
        "stage5b_geometry_caption_v1",
    ] {
        assert!(FIGURE_FLOW_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "tagged_figure_and_caption_link_author_metadata_without_changing_orders",
        "untagged_caption_prefix_links_by_conservative_geometry",
        "artifact_image_retains_context_without_becoming_main_flow_or_warning",
        "equally_plausible_captions_warn_and_remain_unlinked",
        "caption_like_table_cell_is_not_linked_to_author_figure",
    ] {
        assert!(FIGURE_FLOW_TESTS.contains(required), "missing {required}");
    }
    for source in [CLI_LAYOUT_TESTS, PYTHON_LAYOUT_TESTS, WASM_LAYOUT_TESTS] {
        for required in ["stage5b_tagged_figure_v1", "source_node_ids", "author alt"] {
            assert!(source.contains(required), "missing {required}");
        }
    }
    for required in [
        "5/5 passed",
        "9ef3503fdd3b261cd4cd88d5d60f4f9d6067b98365cedc4b6c363a30a2a08d6b",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE5A_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_five_c_navigation_contract_is_safe_bounded_and_cross_frontend() {
    for required in [
        "Link annotations",
        "URI/GoTo actions",
        "named destinations",
        "and outline",
        "entries with stable targets",
        "never executed",
        "max_annotations",
        "max_named_destinations",
        "max_outline_items",
        "navigation_target_invalid",
        "navigation_action_unsupported",
    ] {
        assert!(
            IMAGE_NAVIGATION_SPEC.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "extract_navigation",
        "collect_page_links",
        "collect_name_tree_node",
        "walk_outline_chain",
        "stage5c_link_annotation_v1",
        "stage5c_named_destination_v1",
        "stage5c_outline_v1",
        "navigation_action_unsupported",
        "annotation count limit exceeded",
        "outline item count limit exceeded",
    ] {
        assert!(NAVIGATION_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "links_named_destinations_and_outlines_are_safe_bounded_layout_metadata",
        "malformed_optional_link_preserves_text_and_later_valid_link",
        "navigation_limits_have_exact_and_one_short_boundaries",
        "do-not-run",
        "never.exe",
    ] {
        assert!(NAVIGATION_TESTS.contains(required), "missing {required}");
    }
    for (source, required) in [
        (
            CLI_LAYOUT_TESTS,
            "layout_command_exposes_stage5_navigation_schema",
        ),
        (
            PYTHON_LAYOUT_TESTS,
            "test_stage12_navigation_schema_is_exposed",
        ),
        (WASM_LAYOUT_TESTS, "stage12_navigation_schema_is_exposed"),
    ] {
        assert!(source.contains(required), "missing {required}");
        for field in ["named_destinations", "outlines", "navigation"] {
            assert!(source.contains(field), "missing {field}");
        }
    }
    for required in [
        "LayoutLinkAnnotation",
        "LayoutNamedDestination",
        "LayoutOutlineItem",
        "navigation: navigation_available",
    ] {
        assert!(LAYOUT_IR_SOURCE.contains(required), "missing {required}");
    }
}

#[test]
fn stage_five_completion_evidence_is_private_measured_and_keeps_cutover_closed() {
    for required in [
        "audit_stage5_layout",
        "assert_privacy_safe",
        "private_ir_written\": False",
        "image_placements",
        "unique_image_objects",
        "named_destinations",
        "outline_items",
        "feature_cost_vs_stage4",
        "peak_rss_ratio_vs_stage4",
        "serialized_size_ratio_vs_stage4",
    ] {
        assert!(STAGE5_BENCHMARK.contains(required), "missing {required}");
    }
    assert!(!STAGE5_BENCHMARK.contains("write_private_ir"));
    for required in [
        "Status: PASS",
        "195.707651 pages/s",
        "26.930568x",
        "427,050,863",
        "634695ce886186379ef5efabbfc6900773a59fa64b7fe21ab7a3d753d59c4fef",
        "18/18",
        "Stage 5 is complete",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE5_DOD.contains(required), "missing {required}");
    }
    for required in [
        "Status: Complete; Stage 5C may begin",
        "5/5 passed",
        "951a625e54b36ccaefc0796543d747d4488b9a6dafb396653e2bcb6c262e537f",
    ] {
        assert!(STAGE5B_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_six_shadow_and_streaming_contract_keeps_default_cutover_closed() {
    for required in [
        "LayoutEvent",
        "DocumentFinalize",
        "layout_unrotated_top_left",
        "732,350,054 bytes",
        "monotonic DecodeBudget",
        "byte-identical",
        "Default provider remains PyMuPDF",
    ] {
        assert!(STAGE6_NATIVE_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "default remains `pymupdf`",
        "layout_unrotated_top_left",
        "Stage 6C: page-level or streaming transfer",
        "Default-provider cutover remains forbidden",
        "compact_trace_v1",
    ] {
        assert!(STAGE6_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "TemporaryDirectory",
        "sensitive_comparison_counters",
        "assert_privacy_safe",
        "non_whitespace_character_f1",
        "character_bigram_f1",
        "max_peak_rss_bytes",
        "default_provider_cutover_allowed",
    ] {
        assert!(STAGE6_BENCHMARK.contains(required), "missing {required}");
    }
    for required in [
        "struct LayoutJsonStream",
        "native_events_v2",
        "document_text_omitted",
        "extract_layout_stream",
    ] {
        assert!(
            PYTHON_NATIVE_SOURCE.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "class LayoutStream",
        "def extract_layout_stream",
        "remaining_pages",
    ] {
        assert!(PYTHON_API_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "3.041050x",
        "0.960813",
        "2.805083x",
        "353 passed",
        "658,415,616",
        "native page-production",
        "cutover is forbidden",
    ] {
        assert!(STAGE6AB_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_six_c2a_event_collector_is_exact_but_not_native_page_production() {
    for required in [
        "pub struct LayoutDocumentStart",
        "pub struct LayoutPageFinalization",
        "pub enum LayoutEvent",
        "pub struct LayoutEventStream",
        "pub fn collect_layout_events",
        "pub fn extract_layout_events",
        "pub struct LayoutEventProducer",
    ] {
        assert!(LAYOUT_IR_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "compatibility_event_stream_is_ordered_serializable_and_exact",
        "finalization_patch_restores_delayed_role_and_main_flow_exactly",
        "event_page_and_node_patch_limits_have_exact_boundaries",
        "malformed_event_sequences_and_coordinate_mixing_are_rejected",
    ] {
        assert!(LAYOUT_EVENT_TESTS.contains(required), "missing {required}");
    }
    for required in [
        "Status: Complete; Stage 6C2-B may begin",
        "7/7 PDFs and 1,113/1,113 pages",
        "201.748765 pages/s",
        "674,770,944 bytes",
        "561d797fa9d4e2d93c137d6913d3d4e4527a27bc29df8d0a573441d49fca73db",
        "353/353 passed",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE6C2A_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_six_c2b_page_scopes_text_glyphs_and_content() {
    for required in [
        "pub(crate) struct TextPageProducer",
        "impl Iterator for TextPageProducer",
        "document positioned glyph limit exceeded",
        "producer_delivers_one_page_at_a_time_with_document_ordinals",
        "producer_enforces_the_document_glyph_limit_incrementally",
    ] {
        assert!(TEXT_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "extract_text_page_producer",
        "glyphs: page_glyphs",
        "page_glyphs.iter().zip(&page_glyph_marked_content)",
        "quality.merge(page_quality)",
    ] {
        assert!(LAYOUT_IR_SOURCE.contains(required), "missing {required}");
    }
    assert!(!LAYOUT_IR_SOURCE.contains("glyphs_by_page"));
    assert!(!LAYOUT_IR_SOURCE.contains("extract_text_v2_layout_details"));
    for required in [
        "Status: Complete; Stage 6C2-C may begin",
        "7-document/1,113-page Layout IR parity",
        "212.467582 pages/s",
        "444,100,608 bytes",
        "358e116ae8ede8c7d2dba8e79400adce374842c2daa0bcb5319fbdf647e7e74c",
        "353/353",
        "1.454378x",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE6C2B_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_six_c2c_page_indexes_and_local_semantics_are_exact() {
    for required in [
        "pub(crate) struct PageTaggedStructureIndex",
        "pub(crate) struct TaggedStructureIndex",
        "into_page_index",
    ] {
        assert!(TAGGED_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "apply_page_tagged_tables",
        "apply_page_vector_tables",
        "apply_page_text_tables",
    ] {
        assert!(TABLE_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "apply_page_figure_caption_flow",
        "page_layout.links = self.page_links.next()",
        "validate_tagged_structure_index",
    ] {
        assert!(
            FIGURE_FLOW_SOURCE.contains(required) || LAYOUT_IR_SOURCE.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "Status: Complete; Stage 6C2-D may begin",
        "7-document/1,113-page corpus",
        "182.426213 pages/s",
        "434,147,328 bytes",
        "149f92aaf43a4806a36b76e373fc6dcb9070c08c1f43f289195bcdcbc9f9bcfb",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE6C2C_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_six_c2d_emits_compact_delayed_furniture_finalization() {
    for required in [
        "pub(crate) struct FurnitureFinalization",
        "pub(crate) struct FurnitureCollector",
        "original_role",
        "original_confidence",
        "page_main_flow",
        "apply_furniture_finalization",
    ] {
        assert!(
            READING_ORDER_SOURCE.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "furniture_page_finalizations",
        "into_event_stream_with_finalizations",
        "page_finalizations",
    ] {
        assert!(LAYOUT_IR_SOURCE.contains(required), "missing {required}");
    }
    assert!(LAYOUT_EVENT_TESTS.contains("native_event_api_emits_delayed_furniture_patches"));
    for required in [
        "Status: Complete; Stage 6C2-E may begin",
        "Event tests pass 6/6",
        "1,108,719 bytes",
        "8bfde5151edae46e828aaa27d125073b1e4d94915241da5b7fc874586a6036e1",
        "353/353",
        "genuinely lazy native producer",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE6C2D_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_six_c2e_is_native_lazy_but_keeps_cutover_closed() {
    for required in [
        "pub struct LayoutEventProducer",
        "type Item = PdfResult<LayoutEvent>",
        "fn build_layout_event_producer",
        "pub fn remaining_pages",
        "DocumentFinalize",
    ] {
        assert!(LAYOUT_IR_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "native_events_v2",
        "draining_stable_id_patches_v1",
        "next_finalization_json",
        "pdf_core::LayoutEventProducer",
    ] {
        assert!(
            PYTHON_NATIVE_SOURCE.contains(required),
            "missing {required}"
        );
    }
    assert!(!PYTHON_NATIVE_SOURCE.contains("VecDeque<pdf_core::PageLayout>"));
    for required in ["def finalizations", "remaining_finalizations"] {
        assert!(PYTHON_API_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "native_event_producer_yields_before_a_later_page_error",
        "native_event_producer_can_be_cancelled_after_a_page_without_external_state",
    ] {
        assert!(LAYOUT_EVENT_TESTS.contains(required), "missing {required}");
    }
    for required in [
        "Status: Native lazy production complete; Stage 6C2 acceptance remains NO-GO",
        "7-document / 1,113-page",
        "161.881041 pages/s",
        "1.553468x",
        "900,263,936 bytes",
        "5ac374d01ec0bfeaea88b1595d8f720237a1adb94d0ae7e5fc7169fa48bf3d61",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE6C2E_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_six_d_compacts_documa_metadata_and_closes_the_memory_gate_only() {
    for required in [
        "Privacy-safe Stage 6D profile",
        "rss_peak_bytes_by_phase",
        "metadata_encoded_bytes_sum",
        "contains_extracted_content",
        "contains_source_path",
        "rust_pdf_include_verbose_metadata",
    ] {
        assert!(STAGE6D_PROFILE.contains(required), "missing {required}");
    }
    for required in [
        "Status: Memory and determinism gates complete; default-provider cutover remains NO-GO",
        "compact_trace_v1",
        "34.704637 pages/s",
        "1.056367x",
        "354/354",
        "245966517805ae6d4689355307c7bd12e1f8675b41b87476bc600759a10ac44d",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE6D_DOD.contains(required), "missing {required}");
    }
    assert!(STAGE6_SPEC.contains("Stage 6D memory gate is complete"));
}
#[test]
fn stage_seven_zero_freezes_quality_recovery_before_parser_changes() {
    for required in [
        "normalized character F1 ≥ 0.995",
        "人工 gold pairwise precedence ≥0.95",
        "TEDS-S ≥0.90",
        "occurrence precision/recall",
        "PyMuPDF 僅可作離線 shadow oracle",
        "stage12_page_quality_diff.py",
        "禁止包含文字、原始 character keys、URL、source path 或完整 IR",
    ] {
        assert!(STAGE7_TECHNICAL.contains(required), "missing {required}");
    }
    for required in [
        "# 規格整理 v 1.2.0",
        "## 非技術規格文件",
        "全部通過才切換",
        "不會保存文件原文",
    ] {
        assert!(STAGE7_NONTECHNICAL.contains(required), "missing {required}");
    }
    for required in [
        "Stage 7.0：凍結品質契約與基準",
        "Stage 7.1：建立逐頁 privacy-safe 差距定位",
        "Stage 7.6：補齊整合、回歸與邊界測試",
        "Stage 7.7：文件化、切換與交付",
        "Codex Instructions",
        "Claude Code Instructions",
    ] {
        assert!(STAGE7_AGENT_PLAN.contains(required), "missing {required}");
    }
    for required in [
        "Status: Quality contract complete; Stage 7.1 may begin",
        "four searches and two page reads",
        "0.9446456204",
        "1.056367x",
        "does not open default cutover",
    ] {
        assert!(STAGE7_0_DOD.contains(required), "missing {required}");
    }
}

#[test]
fn stage_seven_one_localizes_quality_without_persisting_private_content() {
    for required in [
        "Privacy-safe Stage 7.1 page-level quality localization",
        "TemporaryDirectory",
        "document_sensitive_comparison_counters",
        "unicode_category_delta",
        "unicode_script_delta",
        "rust_warning_code_counts",
        "temporary_counter_files_removed",
        "contains_character_keys",
        "Stage 7.1 metrics do not reproduce the Stage 6D reference",
    ] {
        assert!(STAGE7A_RUNNER.contains(required), "missing {required}");
    }
    for required in [
        "Status: Page-level localization complete; Stage 7.2 may begin",
        "7 documents / 1,113 pages",
        "0.9608131914224296",
        "0.9512812708818802",
        "438 pages",
        "593 pages",
        "b365b13e643f2fd32c9e386e219c07e11bca23f0bf631958fa3e0a527f266f4e",
        "PyMuPDF remains an offline comparison oracle",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE7A_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_seven_two_separates_raw_parser_quality_from_documa_table_semantics() {
    for required in [
        "raw parser text comparison without Documa table rewriting",
        "pymupdf_raw",
        "rust_layout_source",
        "page_root_character_multiset_matches_source_nodes",
        "temporary_counter_files_removed",
        "quality_rust_vs_pymupdf_raw",
    ] {
        assert!(STAGE7B_RUNNER.contains(required), "missing {required}");
    }
    for required in [
        "Raw parser extraction",
        "Documa adapter integration",
        "Human semantic gold",
        "must not be used as parser text truth",
        "CropBox top-left",
    ] {
        assert!(STAGE7B_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "Status: Raw text completeness gate complete; Stage 7.3 may begin",
        "0.9989543801655596",
        "0.99607461637057",
        "25 pages",
        "155 pages",
        "5281d646379a5c38686b93a510c4af84ebe96d9e4419dd338a13f5c547c14f87",
        "113.0875 pages/s",
        "97.7086 pages/s",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE7B_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_seven_three_requires_reviewed_private_gold_before_order_changes() {
    for required in [
        "Stage 7.3B block-level human reading-order gold",
        "block precedence contains a cycle",
        "must assign every node to exactly one block",
        "requires adjudication reason code",
        "block_pair_concordance",
        "human_order_gold_unconfigured",
        "BLOCKED",
    ] {
        assert!(STAGE7C_RUNNER.contains(required), "missing {required}");
    }
    for required in [
        "reviewer-a",
        "reviewer-b",
        "spanning_heading",
        "sidebar",
        "vertical_note",
        "adjudication_reason_codes",
    ] {
        assert!(STAGE7C_GOLD.contains(required), "missing {required}");
    }
    assert!(STAGE7C_PRIVATE.contains("\"status\": \"unconfigured\""));
    assert!(STAGE7C_PRIVATE.contains("\"documents\": []"));
    for required in [
        "At least two pseudonymous reviewer IDs",
        "Stage 7.4 cannot begin from a `BLOCKED` manifest",
        "Private manifests, candidate orders, page images",
    ] {
        assert!(STAGE7C_REVIEW.contains(required), "missing {required}");
    }
    for required in [
        "Status: Public tooling and private annotation workbench complete; private human gold BLOCKED; Stage 7.4 forbidden",
        "0.3333333333333333",
        "0.888888888888889",
        "96320261853af64d774689e820706f270298c0e396841a45b91278bbef469da3",
        "12abdf537f8d7fa4777238c3647fff56719afd148deba0d0efc528435262308d",
        "d29c12e4dd7d35f51367d57728813ab54a561dfd6d68c273d9a052055992e8e1",
        "Default-provider cutover remains forbidden",
    ] {
        assert!(STAGE7C_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_seven_three_private_review_packet_is_visual_private_and_blocked() {
    for required in [
        "layout_to_display",
        "display_space_from_layout_to_display_v1",
        "blind_neutral_blocks_v2",
        "contains_extracted_text_in_json",
        "must_not_commit",
        "manifest.draft.json",
        "reviewer-a.html",
        "reviewer-b.html",
        "adjudicate.html",
        "separate_locked_html_v2",
        "blind_brush_blocks_erase_split_merge_adjudicate_v2",
        "stage7c-bbox-python-exact",
        "\"schema_version\": 2",
        "\"status\": \"review_required\"",
    ] {
        assert!(STAGE7C_PACKET.contains(required), "missing {required}");
    }
    for required in [
        "pointerdown",
        "pointermove",
        "pointerup",
        "commitBrush",
        "commitErase",
        "commitSplit",
        "mergePrevious",
        "nextOrdinalByPage",
        "reviewerExportObject",
        "mergedExportObject",
        "validateImportedLabels",
        "finalManifestReady",
        "localStorage",
        "qa-manifest",
    ] {
        assert!(STAGE7C_UI.contains(required), "missing {required}");
    }
    for forbidden in [
        "feature_codes",
        "artifact_probability",
        "main_flow_probability",
    ] {
        assert!(!STAGE7C_UI.contains(forbidden), "leaked {forbidden}");
    }
    for required in [
        "Primary task",
        "Task model",
        "State model",
        "Information architecture",
        "Content audit",
        "Deferred blocks",
        "Design and accessibility",
    ] {
        assert!(STAGE7C_UI_SPEC.contains(required), "missing {required}");
    }
    for required in [
        "Status: Stage 7.3C engineering complete; Stage 7.3D pilot may begin; Stage 7.4 remains forbidden",
        "7 documents, 28 selected pages, and 993 visible nodes",
        "390x844",
        "820x900",
        "172 visible nodes",
        "682ffc9ce1e1dd31c4509090b18e782a1098335daf2caa61a599948e452e19d3",
        "abf429fc6a029b8dae3f50c64c9e119b9f0a9f0f1e3e44a6b01b82b709048545",
        "Stage 7.4 remains forbidden",
    ] {
        assert!(STAGE7C_BRUSH_DOD.contains(required), "missing {required}");
    }
}
#[test]
fn stage_seven_three_a_bbox_fidelity_gate_is_explicit_and_complete() {
    for required in [
        "FontVerticalMetrics",
        "DEFAULT_ASCENT",
        "DEFAULT_DESCENT",
        "MAX_VERTICAL_METRIC_MAGNITUDE",
    ] {
        assert!(FONT_METRICS_SOURCE.contains(required), "missing {required}");
    }
    for required in [
        "text_bbox_uses_effective_text_matrix_scale_and_font_vertical_metrics",
        "text_bbox_uses_ctm_scale_and_bounded_default_vertical_metrics",
    ] {
        assert!(
            STAGE12_LAYOUT_IR_TESTS.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "clip_bbox_to_bounds",
        "display_box = clip_bbox_to_bounds",
        "fully invisible boxes are rejected",
    ] {
        assert!(
            STAGE7C_PACKET.contains(required) || STAGE7C_BBOX_DOD.contains(required),
            "missing {required}"
        );
    }
    for required in [
        "Status: Stage 7.3A complete; Stage 7.3B may begin; Stage 7.3C and Stage 7.4 remain forbidden",
        "1,495",
        "993",
        "0.126263%",
        "1.202020%",
        "0.9989543801655596",
        "0a5b0f7b5b1e9d1cba7436d43f8b5cc2593a483303a54de58802277b966d1873",
        "click-per-node v6 workbench is historical engineering",
    ] {
        assert!(STAGE7C_BBOX_DOD.contains(required), "missing {required}");
    }
    assert!(LAYOUT_IR_SOURCE.contains("Type3 FontMatrix"));
}
#[test]
fn stage_seven_three_b_block_gold_schema_and_scorer_are_frozen() {
    for required in [
        "\"schema_version\": 2",
        "\"blocks\"",
        "\"block_precedence_pairs\"",
        "\"internal_order\": \"unspecified\"",
        "reviewer-b-p2-b",
    ] {
        assert!(STAGE7C_GOLD.contains(required), "missing {required}");
    }
    for required in [
        "schema_version=2",
        "Every page node must belong to exactly one",
        "equal-weight average over gold block pairs",
        "block-ID-independent",
        "v1 click-per-node",
        "manifests are superseded",
        "Stage 7.4 cannot begin from a",
    ] {
        assert!(STAGE7C_REVIEW.contains(required), "missing {required}");
    }
    for required in [
        "Status: Stage 7.3B complete; Stage 7.3C may begin; Stage 7.4 remains forbidden",
        "15/15 = 1.0",
        "4/15 = 0.266667",
        "0.888889",
        "918006db44dc4b4149182ba732edab7fb5e63b8a0373e93c7ada2ebe50da22b0",
        "b942589c9381f2ac1d203f7bb2a78a2146a76ff8d98745fc28f24c893cb5756e",
        "38cab2bb8e85fe26ae87dec6e004de8944378c4da4cca2bff2dcd37595ef3216",
    ] {
        assert!(STAGE7C_BLOCK_DOD.contains(required), "missing {required}");
    }
    assert!(STAGE7C_PRIVATE.contains("\"schema_version\": 2"));
    assert!(STAGE7C_RUNNER.contains("canonical_partition"));
    assert!(STAGE7C_RUNNER.contains("score_block_pair"));
}
#[test]
fn stage_seven_three_d_timed_pilot_is_measured_private_and_human_gated() {
    for required in [
        "active_seconds",
        "correction_transactions",
        "undo_transactions",
        "disagreement_pages",
        "reason_counts",
        "manifest_facts",
        "manifest_reviewer_ids",
        "human_order_pilot_unconfigured",
        "stage-7.3d-timed-blind-pilot",
        "stage_7_4_gate_review_allowed",
    ] {
        assert!(STAGE73D_PILOT.contains(required), "missing {required}");
    }
    for required in [
        "real two-reviewer pilot pending",
        "active time only",
        "No arbitrary speed threshold",
        "Stage 7.4",
    ] {
        assert!(STAGE73D_SPEC.contains(required), "missing {required}");
    }
    assert!(STAGE73D_EXAMPLE.contains("\"status\": \"unconfigured\""));
    assert!(STAGE73D_EXAMPLE.contains("\"sessions\": []"));
    for required in [
        "Status: Stage 7.3D tooling complete; real two-reviewer pilot BLOCKED; Stage 7.4 forbidden",
        "a5ac0eeb099be5dfd8648a7f7350db50ed9a49f167eb8abbd2318fa644846bc6",
        "d64f69a8d11630e2dee24b26eb883b985563cd9afd7c080350b86dbdb8dfd174",
        "Synthetic QA cannot satisfy this gate",
        "Stage 7.4 is forbidden",
    ] {
        assert!(STAGE73D_DOD.contains(required), "missing {required}");
    }
}
