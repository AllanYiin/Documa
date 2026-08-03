# Stage 12 Stage 3 Definition of Done

Status: PASS
Date: 2026-07-29

## Implemented contract

- `pdf-core` owns deterministic span-to-line clustering, bounded recursive XY-cut,
  paragraph/list grouping, strict page-number recognition, repeated Header/Footer
  classification, and main-flow exclusion.
- All decisions use `layout_unrotated_top_left`; source, tagged, inferred, and
  main-flow orders remain independent.
- Rotated/vertical text and XY-cut depth exhaustion preserve content through a
  deterministic warned fallback.
- Bindings only expose the shared Layout IR. Rust, CLI, Python, and browser WASM
  do not contain duplicate PDF-aware reading-order logic.

## Synthetic and compatibility evidence

- Focused reading-order tests: 14/14 PASS (13 integration tests plus 1 internal
  exact-depth-boundary test).
- Supported single/two/three-column, paragraph, list, CJK, repeated furniture,
  strict Arabic/Roman page-number, Artifact, author-role, rotation, and exact span
  limit cases pass at 1.0 expected order/boundary/role accuracy.
- Stage 2 tagged-structure regressions remain 13/13; Stage 1B Layout IR remains 3/3.
- Stage 12 executable contract tests pass 13/13 and CLI Layout IR tests pass 2/2.
- Exact final Python wheel tests pass 7/7; Node/WASM tests pass 7/7.

## Frozen private corpus evidence

Command:

```text
python tools/stage12_reading_order_benchmark.py --corpus-dir C:\Users\allan\Music
```

The run used one warm-up and three measured runs per document.

- 7 documents, 1,113 pages, 61,414 paragraph/list/semantic nodes, 133,503 spans.
- 5.348794 seconds summed document medians; 208.084305 pages/s.
- 28.633671x versus the frozen Stage 0 complete Documa adapter baseline.
- 330,906,731 serialized bytes for one run of every document.
- 7/7 deterministic output, schema audits, and privacy audits.
- 423 tagged pages and 37,171 tagged nodes.
- Inferred-vs-tagged pairwise proxy: 0.940546; source proxy: 0.927516.
- Main-flow coverage: 0.981926; 389 Header, 189 Footer, 107 PageNumber,
  425 Artifact, and 3,396 ListItem nodes.
- Report: `target/stage12-reading-order-benchmark/report.json`, 19,255 bytes.
- Report SHA-256:
  `be92154b73b87f7de9b803c1ed2375a33b143c5b33db9108ebe130cd4693f6c6`.
- The report contains no extracted text, semantic-node payloads, debug glyphs,
  Alt, ActualText, tags, fingerprints, or private IR.

The 0.940546 proxy is below the 0.95 cutover target. Stage 3 is complete because
its implementation and evidence contract is satisfied, but this quality result
independently forbids changing Documa's default provider.

## Release-shaped artifacts

- Python wheel: 889,398 bytes, SHA-256
  `5318ef36b05a405bd321bf5edd5608d42cb32b23770cfdccb3addc221c3374d5`.
- Browser WASM: 1,196,509 bytes, SHA-256
  `f8b5e2597cc1ddb7bd2c8a26ac877a9cc43d6d882147c50eb15eb1e8f3d06978`.

## Stage gate

- `cargo fmt --all --check`: PASS.
- Workspace all-target Clippy with `-D warnings`: PASS.
- wasm32 all-target Clippy with `-D warnings`: PASS.
- `cargo test --workspace`: PASS.
- Python and Node/WASM exact-artifact/interface tests: PASS.

Stage 3 is complete. Stage 4 table reconstruction may begin. Default-provider
cutover remains forbidden until the reading-order threshold and all later table,
Documa shadow, integration, rollout, and rollback gates pass.