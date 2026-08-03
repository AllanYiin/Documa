# Stage 12: Documa PDF Parser Replacement

Status: Stage 0 through Stage 6D and Stage 7.2 implemented; Stage 7.3 tooling and private review packet ready but two human reviews are incomplete/BLOCKED; Stage 7.4 forbidden; default cutover remains NO-GO.

This program replaces the PDF-aware part of Documa's PyMuPDF adapter with the
shared Rust `pdf-core`. It does not move document-level semantics, chunking,
search, citation presentation, or LLM decisions into Rust.

## Stage order

0. Freeze corpus, baseline, contracts, privacy rules, and go/no-go metrics.
1. 1A: canonical page geometry; 1B: public Layout IR and binding schema.
2. Tagged structure, MCID, ActualText, Alt, and Artifact handling.
3. Human reading order, paragraphs, and repeated page furniture.
4. Tables and cell topology.
5. Image placements and navigation metadata.
6. Documa Rust shadow adapter and parity reports.
7. Integration, regression, fuzzing, and resource limits.
8. Documentation, rollout, default-provider cutover, and rollback removal.

Every stage is gated by its focused tests, `cargo fmt --all --check`, Clippy
with warnings denied, workspace tests, and an explicit Definition of Done.

## Stage 0 artifacts

- `technical-spec.md`: architecture, metrics, and release gates.
- `coordinate-system.md`: mandatory coordinate convention for Stage 1A onward.
- `tests/fixtures/stage12/baseline-contract.json`: private corpus manifest.
- `tools/stage12_baseline.py`: reproducible speed and quality baseline.
- `tests/fixtures/stage12/stage0-dod.md`: completion evidence and current gaps.

## Stage 1A artifacts

- `crates/pdf-core/src/geometry.rs`: canonical coordinate types and transforms.
- `crates/pdf-core/tests/stage12_geometry.rs`: synthetic geometry and page-tree coverage.
- `crates/pdf-cli/tests/geometry.rs`: executable CLI JSON contract.
- `tools/stage12_coordinate_parity.py`: privacy-safe PyMuPDF parity runner.
- `tests/fixtures/stage12/stage1a-dod.md`: completion evidence and remaining boundary.

## Stage 1B artifacts

- `docs/specs/stage-12/layout-ir-schema.md`: versioned Layout IR contract.
- `crates/pdf-core/src/layout_ir.rs`: coordinate-normalized shared DTO and builder.
- `crates/pdf-core/tests/stage12_layout_ir.rs`: deterministic schema and geometry coverage.
- `tools/stage12_layout_benchmark.py`: privacy-safe schema and throughput audit.
- `tests/fixtures/stage12/stage1b-dod.md`: completion evidence and Stage 2 boundary.

## Stage 2 artifacts

- `docs/specs/stage-12/tagged-structure.md`: tagged-structure and marked-content contract.
- `crates/pdf-core/src/tagged_structure.rs`: bounded StructTreeRoot, RoleMap, and ParentTree traversal.
- `crates/pdf-core/tests/stage12_tagged_structure.rs`: metadata, malformed, cycle, limit, and recovery coverage.
- `tools/stage12_tagged_benchmark.py`: privacy-safe tagged schema and throughput audit.
- `tests/fixtures/stage12/stage2-dod.md`: completion evidence and Stage 3 boundary.

## Stage 3 artifacts

- `docs/specs/stage-12/reading-order.md`: human reading-order and page-furniture contract.
- `crates/pdf-core/src/reading_order.rs`: bounded line, XY-cut, paragraph, list, and furniture rules.
- `crates/pdf-core/tests/stage12_reading_order.rs`: synthetic order, role, fallback, and limit coverage.
- `tools/stage12_reading_order_benchmark.py`: privacy-safe throughput and tagged-order proxy audit.
- `tests/fixtures/stage12/stage3-dod.md`: completion evidence and Stage 4 boundary.

## Stage 4 artifacts

- `docs/specs/stage-12/table-reconstruction.md`: tagged, ruled, borderless, and fused table contract.
- `crates/pdf-core/src/table_reconstruction.rs`: bounded table topology and source-node mapping.
- `crates/pdf-core/tests/stage12_table_reconstruction.rs`: exact topology, alias, empty, recovery, and limit coverage.
- `tests/fixtures/stage12/stage4a-dod.md`: Stage 4A completion evidence and Stage 4B boundary.
- `tests/fixtures/stage12/stage4b-dod.md`: Stage 4B completion evidence and Stage 4C boundary.
- `tests/fixtures/stage12/stage4c-dod.md`: Stage 4C completion evidence and Stage 4D boundary.
- `tools/stage12_table_benchmark.py`: privacy-safe table schema, speed, memory, size, and determinism audit.
- `tests/fixtures/stage12/stage4-dod.md`: final Stage 4 completion evidence and Stage 5 boundary.

## Stage 5 artifacts

- `docs/specs/stage-12/image-placement-navigation.md`: image occurrence, figure/caption, and navigation contract.
- `crates/pdf-core/src/vector_paths.rs`: shared bounded vector/Form traversal and painted image occurrence collector.
- `crates/pdf-core/tests/stage12_image_placements.rs`: exact image Quad/BBox/resource/ordinal/recovery/limit coverage.
- `tests/fixtures/stage12/stage5a-dod.md`: Stage 5A completion evidence and Stage 5B boundary.
- `crates/pdf-core/src/figure_flow.rs`: tagged Figure precedence and conservative caption anchoring.
- `crates/pdf-core/tests/stage12_figure_flow.rs`: positive, Artifact, ambiguity, table-exclusion, and order-preservation coverage.
- `tests/fixtures/stage12/stage5b-dod.md`: Stage 5B completion evidence and Stage 5C boundary.
- `crates/pdf-core/src/navigation.rs`: bounded Link, destination name-tree, and outline extraction.
- `crates/pdf-core/tests/stage12_navigation.rs`: target, geometry, unsupported-action, recovery, and limit coverage.
- `tests/fixtures/stage12/stage5c-dod.md`: Stage 5C completion evidence and Stage 5D boundary.
- `tools/stage12_image_navigation_benchmark.py`: privacy-safe image/navigation speed, memory, size, schema, and determinism audit.
- `tests/fixtures/stage12/stage5-dod.md`: final Stage 5 evidence and Stage 6 boundary.
## Stage 6 artifacts

- `docs/specs/stage-12/documa-shadow-adapter.md`: replacement boundary, mapping, shadow, streaming, and rollback contract.
- `docs/specs/stage-12/native-page-production.md`: event-stream refactor, frozen coordinate/semantic boundaries, 1.2x RSS gate, and exact-parity acceptance criteria.
- `tools/stage12_documa_shadow.py`: privacy-safe complete-adapter speed, RSS, determinism, coordinate, and quality comparison.
- `bindings/python/src/lib.rs`: draining `LayoutJsonStream` that releases each serialized page.
- `bindings/python/python/rust_pdf/__init__.py`: public `extract_layout_stream()` iterator with metadata.
- Documa `RustPdfAdapter`: explicit provider mapping, inferred-order lock, old-wheel fallback, draining stream preference, and default `compact_trace_v1` metadata with reversible verbose opt-in.
- `tests/fixtures/stage12/stage6ab-dod.md`: Stage 6A/6B completion, Stage 6C transfer evidence, and current NO-GO decision.
- `crates/pdf-core/tests/stage12_layout_events.rs`: event ordering, collector
  parity, finalization patch, coordinate, and exact-limit coverage.
- `tests/fixtures/stage12/stage6c2a-dod.md`: Stage 6C2-A event/collector evidence
  and the Stage 6C2-B boundary.
- `crates/pdf-core/src/text.rs`: page-scoped text/glyph/content producer with
  shared document runtime and incremental limits.
- `tests/fixtures/stage12/stage6c2b-dod.md`: Stage 6C2-B exactness, memory,
  artifact, Documa, and Stage 6C2-C handoff evidence.
- `tests/fixtures/stage12/stage6c2c-dod.md`: Stage 6C2-C page-indexed and
  page-local semantic exactness evidence.
- `tests/fixtures/stage12/stage6c2d-dod.md`: Stage 6C2-D compact furniture
  finalization, artifact, Documa, and Stage 6C2-E handoff evidence.

- `tests/fixtures/stage12/stage6c2e-dod.md`: native lazy producer, Python/Documa finalization drain, exactness, performance, and NO-GO evidence.
- `tools/stage12_documa_metadata_profile.py`: privacy-safe parse/metadata/serialization lifecycle RSS and field-size profiler.
- `tests/fixtures/stage12/stage6d-dod.md`: compact metadata, citation trace, three-run memory/determinism gate, and remaining quality NO-GO evidence.
- `docs/specs/stage-12/quality-recovery-technical.md`: Stage 7 text/order/table/image quality contract and privacy boundary.
- `docs/specs/stage-12/quality-recovery-nontechnical.md`: stakeholder-facing quality and cutover explanation.
- `docs/specs/stage-12/quality-recovery-agent-plan.md`: Stage 7.0 through 7.7 Codex/Claude Code execution plan.
- `tests/fixtures/stage12/stage7-0-dod.md`: research, frozen quality contract, and Stage 7.1 handoff evidence.
- `tools/stage12_page_quality_diff.py`: Stage 7.1 process-isolated, temporary-counter page quality localization and privacy audit.
- `tests/fixtures/stage12/stage7a-dod.md`: exact Stage 6D score reproduction, worst-page evidence, and Stage 7.2 handoff.
- `tools/stage12_parser_text_quality.py`: Stage 7.2 raw PyMuPDF versus Rust Layout source-text comparator, isolated from table rewriting.
- `docs/specs/stage-12/parser-text-quality.md`: raw parser, adapter integration, and human-gold metric boundaries.
- `tests/fixtures/stage12/stage7b-dod.md`: corrected raw text gate, privacy evidence, and Stage 7.3 handoff.
- `tools/stage12_order_gold.py`: reviewed precedence/main-flow/artifact validator, scorer, privacy guard, and BLOCKED state.
- `tools/stage12_order_review_packet.py`: deterministic private-page selection, LayoutSpace-to-DisplaySpace overlays, clean renders, and isolated reviewer/adjudication workspaces.
- `tools/stage12_order_review_ui.html`: standalone click-order/artifact annotation workbench with import validation, reviewer-only exports, adjudication, recovery storage, and synthetic browser QA.
- `docs/specs/stage-12/order-review-ui.md`: primary task, task/state model, information architecture, content visibility, accessibility, responsive, and verification contract.
- `docs/specs/stage-12/order-gold-review.md`: two-reviewer private packet, annotation, import/merge, and coded adjudication workflow.
- `tests/fixtures/stage12/quality/order/`: public exact gold/candidates and unconfigured private manifest template.
- `tests/fixtures/stage12/stage7c-dod.md`: public scoring, private packet QA evidence, and explicit human-review blocker.
