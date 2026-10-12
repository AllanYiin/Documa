# DEVNOTE

> Imported standalone history. Historical artifact paths refer to the original standalone working copy. Current Documa integration status is in ../../DEVNOTE.md and ../../docs/PDF_PRIORITY_INTEGRATION_20261012.md.

## SNAPSHOT

- 2026-10-12 P0-P2 engineering priorities are complete: bounded extra filters, vertical CID W2/DW2, conservative/lazy OCR with budgets, selection/limits, reusable Python handles with GIL release, CLI NDJSON, WASM streams, expanded CI/fuzz and pinned ONNX integration. Standalone validation: Rust 264 passed / 1 manual ignored; Python 19; Node WASM 14; ONNX 7 cases; 200,565 fuzz runs without crashes. Human reading-order/table/image acceptance remains deferred. See docs/priority-implementation-2026-10-12.md.
- The user authorized chronological topic commits and submission to Documa main. The complete public source is now integrated into Documa/native/pdf and the installable Python API into Documa/src/rust_pdf; temporary/private assets and local binaries are excluded. The standalone directory remains outside Git. No package-registry release or installed-runtime replacement is part of this submission.

- 2026-10-12: Backported Documa-only PDF core fixes (CTM/Form composition, positional word spaces, rotated margin notes, narrow-column and paragraph spacing) plus the new bounded Encoding/Differences indirect-reference fix. Preserved the standalone OCR/CLI/Python/WASM capabilities. Standalone workspace tests: 236 passed / 1 existing manual benchmark ignored; fmt and all-target Clippy -D warnings PASS. Documa source adapter integration: 81 passed. Direct source-built CPython 3.13 binding parsed the original 19-page PDF with rust_pdf, no warnings/U+FFFD, unchanged source hash. Local source only; no Git repository, release, installed runtime replacement, or remote mutation. Audit: docs/NATIVE_UPSTREAM_SYNC_20261012.md. Existing human-gold/cutover gates remain unchanged.

- Active goal completed locally: text-extraction-first PDF parser Stages 0 through 11.7.
- Stage 12 Documa replacement program: Stage 0 through Stage 6D and Stage 7.2 are
  implemented. Stage 7.3A BBox fidelity, Stage 7.3B block gold schema/scorer,
  Stage 7.3C blind brush workbench, and Stage 7.3D timed-pilot tooling are complete.
  The legacy v1 validator/scorer and v6 click-per-node workbench remain historical
  engineering evidence only. Two independent real human reviews, adjudication, and
  timed pilot remain BLOCKED, so Stage 7.4 is forbidden.
  Native lazy production and compact mapping pass memory at 1.056367x, and raw
  parser character F1 0.998954 passes the text gate. Default cutover remains
  NO-GO on human reading-order and private table/image gold.
- An additive `PageLayout.visual_reading` attention graph is implemented locally: semantic
  blocks are simultaneous attention units, multiple focus candidates and continue/skip/regression
  edges preserve non-linear paths, and peripheral/Artifact blocks remain present but skippable.
  It does not replace any of the four orders, open Stage 7.4, or claim human-gold validation.
- Current public release candidate is `0.2.0`; `version_info().stage` is `stage-11` on Rust,
  CLI, Python, and browser WASM.
- Direct-image OCR is implemented additively. pdf-core conservatively selects a
  single full-page painted Image XObject without rasterization and maps pixel quads
  to LayoutSpace; pdf-ocr provides bounded JPEG/raw decoding plus an optional
  RapidOCR/PaddleOCR-compatible ONNX backend. CLI/Python expose native OCR behind
  ocr-onnx; WASM exposes candidates for a browser-side inference backend.
- Deliverables: verified Rust core crate, standalone CLI archive, Python wheel, and browser WASM package.
- Stage 11 adds ContentOrder／legacy Layout／script-aware Auto, positioned glyphs, font metrics,
  ActualText, quality metadata, document DecodeBudget, and bounded object-stream LRU cache.
- This standalone workspace is not a Git repository; source commits are managed in the Documa repository. No tag or registry publication is requested.
- Both supplied private PDFs passed SHA, inspect, validate, all three extraction modes, and
  required/forbidden text fragments; neither private PDF is stored in the repository.

## DECISIONS

- `crates/pdf-core` is the only PDF-aware implementation layer.
- Human visual reading is represented separately from linear text orders. `visual_reading`
  uses one semantic text node per visual block with `internal_order=simultaneous`, up to three
  salience-ranked focus candidates, and bounded weighted continue/skip-ahead/regression edges.
  Scores are deterministic heuristics, not calibrated gaze probabilities; bindings serialize only.
- No existing PDF parser or native PDF engine dependency is allowed.
- Reliable Unicode text extraction is the primary product goal; page rendering is absent.
- ToUnicode is authoritative. Explicit, warned fallback handles supported missing-CMap cases.
- Layout is a coordinate heuristic, not rendering output; NFC is opt-in.
- The priority-2026-10-12 development line supports Flate, ASCIIHex, ASCII85, RunLength and LZW general stream filters with bounded chains/output; the legacy version remains `0.2.0`.
- JPEG Image XObjects are codec-validated and preserved; other codecs may be preserved with warnings.
- General page rendering, encryption, editing, damaged-xref repair, and most
  image/filter codecs remain unsupported. OCR is limited to the conservative
  direct-image path; it does not silently rasterize rejected pages.
- OCR models and dictionaries are external artifacts and are never bundled.
- All public errors carry stable machine-readable codes; all input-derived work is bounded.
- Workspace default-feature MSRV is Rust 1.88; optional ONNX builds require Rust 1.89 due to transitive dependencies. Both feature configurations were checked with their stated compiler.
- PyO3 remains at 0.29+ to avoid advisories affecting the earlier 0.27 line.
- `Auto` groups glyphs by rotation/writing mode, uses page-space advance for line clustering,
  preserves raw glyph Unicode, and applies Unicode-standard CJK compatibility decomposition only
  to the synthesized readable text.
- Every `PdfDocument` owns one clone-shared monotonic DecodeBudget and deterministic bounded LRU
  object-stream cache; xref decoding is charged before document construction completes.
- Structural expansion-ratio exceptions are limited to verified XRef/ObjStm streams; absolute
  stream and document-lifetime limits remain authoritative.
- Public and heuristic table geometry is LayoutSpace: normalized CropBox top-left,
  x right, y down, points after UserUnit, and page Rotate not applied. PDF CTM/Form
  Matrix is applied before exactly one `pdf_to_layout` projection.
- Private review overlays transform frozen LayoutSpace boxes through each page's
  `layout_to_display` matrix exactly once, then scale to rendered pixels. Static
  evidence is box-only; the local HTML adds small sequence badges and on-hover IDs.
- Horizontal text BBoxes use FontDescriptor Ascent/Descent or bounded 800/-200
  defaults after effective text-matrix/CTM projection. Public text DTOs are unchanged;
  Type3 FontMatrix, glyph outlines, and true vertical writing remain estimated.
- Layout IR preserves geometric BBoxes that cross CropBox. Private review overlays
  clip only their display representation to visible page bounds; they do not rewrite IR.
- Stage 7.3 human review moves to block gold schema v2. Brush strokes are transient
  input gestures; persisted truth is node-to-block membership, block role, and block
  precedence. The legacy click-per-node v1 UI must not be used for real review.
- Stage 7.3 human gold uses separate locked Reviewer A/B workspaces. Reviewer-only
  exports blank the other reviewer and adjudication; the adjudicator imports both,
  and `status=complete` is emitted only after full coverage and coded disagreements.
- Stage 7.3D keeps timing and interaction burden outside the gold manifest. A separate
  bounded pilot log records only active seconds and aggregate action counts, while the
  manifest remains the sole human truth. Synthetic QA can never satisfy the human gate.
- Table evidence precedence is tagged topology, compatible vector lattice, then
  conservative text alignment. Lower evidence may refine geometry but cannot
  overwrite author spans/header roles; material conflict preserves stronger data.
- Rust owns deterministic geometric/structural table parsing. Domain semantics,
  cross-page merge, chunking, and LLM reasoning remain in Documa.
- Image occurrences use the same unrotated top-left LayoutSpace. Unit-square corners
  retain identity through content/Form CTMs before one page projection. Page-local
  image paint ordinals are a separate domain from text glyph source ordinals.
- Rust retains marked Figure/MCID/Alt/Artifact evidence and conservative caption
  anchors; semantic nodes/orders are never rewritten. LLM semantic interpretation
  remains in Documa.
- Rust owns bounded Link annotations, URI/GoTo targets, named destinations, and
  outline traversal. Unsafe or unknown actions are descriptive metadata only and
  are never executed; malformed optional navigation does not suppress visible text.
- Stage 5 increases serialized Layout IR size by 29.0143% versus Stage 4. Stage 6
  must prefer page-level or streaming transfer rather than retaining full JSON bytes
  and a decoded Python object graph simultaneously.

- Python `extract_layout_stream()` uses `native_events_v2` and owns the fallible Rust
  producer directly. Accepted pages are released immediately; metadata finalizes in
  place and `draining_stable_id_patches_v1` releases terminal page patches individually.
- Documa owns provider defaults (currently auto/Rust-first in its repository) and retains PyMuPDF for OCR/page-preview rendering and explicit fallback. Rust replaces parser extraction; cross-page/domain/LLM semantics remain in Documa. This submission does not change those defaults.
- `LayoutEventProducer` owns clone-shared parser state plus compact tagged/navigation,
  warning, quality, table, and furniture state. It yields `DocumentStart`, page-local
  events, then `DocumentFinalize`; later-page errors surface after earlier delivery.
- Layout production uses one owned `TextPageProducer` and drops page glyphs,
  marked-content, operations, and `PageLayout` payloads after consumer acceptance.
- Repeated furniture is a compact stable-ID role/main-flow finalization. Documa applies
  patches before returning `DocumentIR`; no complete native page queue is retained.
- Documa defaults Rust mapping to `compact_trace_v1`: one shared schema covers source
  ordinals, MCIDs, text origins, and rule ID; page object/coordinate space are inherited.
  Stable refs and semantic evidence remain, and `rust_pdf_include_verbose_metadata=True`
  restores the previous verbose shape.- Native optional-vector streaming recovery is fail-forward: accepted pages cannot be
  retracted. Limit failures terminate; later non-limit vector failure disables optional
  vectors and finalizes a stable warning.

## VALIDATION
- Direct-image OCR validation on 2026-08-06: focused core candidate/mapping tests
  2/2 and pdf-ocr raw decoder tests 2/2 pass. cargo test --workspace
  --all-features, cargo fmt --all --check, and workspace all-target/all-feature
  Clippy with -D warnings all pass. Default and ocr-onnx builds were checked
  for CLI and Python; WASM candidate bindings compile. Python package source also
  passes an in-memory syntax compile. No external ONNX model fixture was bundled,
  so model accuracy remains an external integration acceptance item.

- Additive visual-reading validation on 2026-07-30: Stage 12 reading-order 15/15 and
  contract 33/33 pass; `cargo fmt --all --check`, native workspace all-target/all-feature
  Clippy `-D warnings`, wasm32 core/WASM Clippy `-D warnings`, and
  `cargo test --workspace --all-features` including doctests all pass.
- Stage 12 Stage 0 formal baseline verified 7 private PDFs / 1,113 pages with one warm-up
  and three measured runs for PyMuPDF raw, current Documa, and Rust release CLI. All 21
  parser/document groups were deterministic. The non-private report SHA-256 is
  `1d6435fe105a527f3889073bf8c7fc4dd152c92772ea68f5db38ae0912daf00c`.
- Formal Stage 0 throughput: PyMuPDF raw 110.149 pages/s, Rust 50.805 pages/s, current
  complete Documa adapter 7.267 pages/s; Rust speedup versus Documa is 6.991x.
- Stage 0 focused contract tests 3/3, `cargo fmt --all --check`, workspace all-target
  Clippy `-D warnings`, and `cargo test --workspace` all passed on 2026-07-29.
- Stage 12 Stage 1A formal coordinate parity verified 7 private PDFs / 1,113 pages
  against PyMuPDF with zero mismatches at 0.5 pt tolerance. Maximum dimension delta
  was 0.000029297 pt; report SHA-256 is
  `ebbaba38b3f818369b53895a24e7b25784e774f9a8f2c834f081cd484de36b13`, and
  `contains_extracted_content` is false.
- Stage 1A focused geometry 7/7, Stage 12 contract 4/4, CLI geometry 1/1,
  `cargo fmt --all --check`, workspace all-target Clippy `-D warnings`, and
  `cargo test --workspace` all passed on 2026-07-29. Six generated test fixtures
  missing mandatory MediaBox values were corrected; no implicit page size was added.
- Stage 12 Stage 1B formal Layout IR benchmark verified 7 private PDFs / 1,113 pages
  with one warm-up and three measured runs. All 7 groups were byte-deterministic;
  schema and privacy audits passed 7/7. Throughput was 274.353900 pages/s and
  37.752772x versus the frozen Stage 0 Documa adapter baseline. The privacy-safe
  report SHA-256 is `00f244b330419058e13dd019a6e9c88aeebfab58bd084bc5f7c75b5e29a49345`.
- Stage 1B emitted 1,113 nodes and 133,503 spans. One serialized run per document
  totals 271,882,351 bytes; page-level or streaming transfer remains an integration
  optimization before cutover, not a Stage 1B schema blocker.
- Stage 1B focused core 3/3, Stage 12 contract 6/6, CLI 1/1, Python 6/6,
  Node WASM 6/6, `cargo fmt --all --check`, workspace all-target Clippy `-D warnings`,
  and `cargo test --workspace` all passed on 2026-07-29.
- Stage 1B artifacts: Python wheel SHA-256
  `d3539523dac5354021f37c76c06b99271785248f9da9fc7d685edc2f44c43d8d` and
  browser WASM SHA-256 `fe8cc4184a69872df9d2047f702d7c78a872db84474b9b5b6c514242bc864a2c`.
- Stage 12 Stage 2 formal tagged-structure benchmark verified 7 private PDFs / 1,113
  pages with one warm-up and three measured runs. All 7 groups were byte-deterministic;
  schema and privacy audits passed 7/7. Throughput was 218.501999 pages/s and
  30.067210x versus frozen Stage 0 Documa. Report SHA-256 is
  `919b5be5995433aa9b3e970303255ebec9fdacad1278e459c8e3718070f41ff9`.
- Stage 2 found 423 tagged pages, 15,360 tagged nodes, 15,146 associated MCIDs,
  and 423 artifacts. Six untagged documents remained byte-identical to Stage 1B;
  the tagged AI Index added 17,173,071 serialized bytes.
- Stage 2 focused core 13/13, contract 10/10, CLI 2/2, Python wheel 7/7,
  Node WASM 7/7, native and wasm32 Clippy `-D warnings`, formatting, and full
  workspace tests all passed on 2026-07-29. Stage 3 may begin.
- Stage 12 Stage 3 formal reading-order benchmark verified 7 private PDFs / 1,113
  pages with one warm-up and three measured runs. All 7 groups were deterministic;
  schema and privacy audits passed 7/7. Throughput was 208.084305 pages/s and
  28.633671x versus frozen Stage 0 Documa. Tagged-order proxy was 0.940546, below
  the 0.95 cutover target, so default-provider cutover remains forbidden. Report
  SHA-256 is `be92154b73b87f7de9b803c1ed2375a33b143c5b33db9108ebe130cd4693f6c6`.
- Stage 3 focused reading-order tests 14/14 including the internal depth boundary,
  contract 13/13, CLI 2/2, Python wheel 7/7, Node WASM 7/7, native and wasm32
  Clippy `-D warnings`, formatting, and full workspace tests passed on 2026-07-29.
  Stage 4 may begin.
- Stage 12 Stage 4A preserves bounded Table/TR/TH/TD hierarchy, RoleMap aliases,
  RowSpan/ColSpan, header Scope, honest optional empty-cell geometry, structure
  objects, provenance, and source-node links. Focused table tests pass 4/4 and
  Stage 12 contract tests pass 14/14.
- Stage 4A `cargo fmt --all --check`, native workspace all-target/all-feature
  Clippy `-D warnings`, wasm32 core/WASM Clippy `-D warnings`, and full workspace
  tests all passed on 2026-07-29.
- Stage 4B/4C add bounded vector-lattice and conservative borderless detectors.
  Stage 4D adds deterministic tagged/vector fusion, conflict precedence, per-page
  shared parsed operations, CJK/multiline/mixed-size/rotation coverage, and exact
  nonempty table parity through Rust, CLI, Python, and WASM.
- Final Stage 4 focused table tests 16/16, contract 14/14, CLI 3/3, exact wheel
  tests 8/8, Node WASM tests 8/8, formatting, native/wasm32 Clippy `-D warnings`,
  and full workspace tests passed on 2026-07-29.
- Formal Stage 4 benchmark: 7 PDFs / 1,113 pages, one warm-up plus three measured
  runs, 205.731635 pages/s, 28.309930x frozen Documa, 0.988694x Stage 3 throughput,
  674,615,296-byte maximum sampled RSS, and 331,010,437 serialized bytes. It found
  5 tables / 76 cells (fused 1, vector 2, text alignment 2); all groups were
  deterministic and schema/privacy safe. Report SHA-256 is
  `2a49875c7da1bf81145e27cad299f4b24a9c97ab4ef46e2193efe55d12e73de8`.
  Private TEDS-S is null because no table gold labels exist; synthetic exact
  fixtures imply TEDS-S 1.0.
- Stage 4 artifacts: CPython 3.10 wheel SHA-256
  `4133713b7696b1e97891cdff44d2096b56046f356f23459461e2364d6e87b088`;
  browser WASM SHA-256
  `50ec697c7897905e439957f41f0824b4e1fabe30aee34323fbf823c2d9252931`.
- Stage 5A image placement focused tests 5/5 and Stage 5B figure/caption tests
  5/5 pass. Exact CropBox/UserUnit/Rotate, q/Q/cm, nested Form, repeated paint,
  invalid recovery, Artifact, tagged Figure/Caption, ambiguity, table exclusion,
  and occurrence-boundary cases are covered.
- Stage 5B contract 16/16, CLI layout 4/4, exact CPython wheel 6/6, Node WASM
  7/7 plus web 2/2, formatting, native/wasm32 Clippy `-D warnings`, and full
  workspace tests all passed on 2026-07-29.
- Stage 5B CPython 3.10 wheel: 988,466 bytes, SHA-256
  `951a625e54b36ccaefc0796543d747d4488b9a6dafb396653e2bcb6c262e537f`.
- Stage 5C navigation focused tests 3/3, contract 17/17, CLI 5/5, exact
  CPython wheel 7/7, Node WASM 8/8 plus web 2/2, formatting, native/wasm32
  Clippy `-D warnings`, and full workspace tests passed on 2026-07-29.
- Formal Stage 5D benchmark: 7 PDFs / 1,113 pages, one warm-up plus three measured
  runs, 195.707651 pages/s, 26.930568x frozen Documa, and 0.951276x Stage 4
  throughput (4.8724% feature cost). Maximum RSS was 675,053,568 bytes
  (1.000650x Stage 4); serialized bytes were 427,050,863 (1.290143x Stage 4).
  It found 76,336 image placements / 2,068 unique objects, 169 tagged figures,
  48 caption links, 3,468 links, 1,393 named destinations, and 136 outline items.
  All groups were deterministic and schema/privacy safe. Report SHA-256 is
  `634695ce886186379ef5efabbfc6900773a59fa64b7fe21ab7a3d753d59c4fef`.
- Final Stage 5 focused contract 18/18, formatting, native/wasm32 Clippy
  `-D warnings`, and `cargo test --workspace --all-features` passed. Final wheel:
  1,026,878 bytes, SHA-256 `290a07aab92b1cdcadbf7693b87d8b2490338840e607546d72e712cc8af55cca`;
  browser WASM: 1,410,238 bytes, SHA-256
  `8fdd9ba4bb217486df9eb1d81feec7eca8277d04837e5e4e5580f3c29dc18f88`.
  Stage 6 may begin; default-provider cutover remains forbidden.

- Stage 6A/6B formal complete-adapter shadow: 7 PDFs / 1,113 pages, Rust
  20.095623 pages/s versus PyMuPDF Documa 6.608120 pages/s (3.041050x).
  Character F1 is 0.960813 and bigram F1 is 0.951281. Rust peak RSS is
  1,711,919,104 bytes (2.805083x PyMuPDF) and canonical Documa IR is 3.496819x.
  Both providers are deterministic; page counts and invalid bboxes pass. Report SHA-256:
  `21142e2c2f6db995336c8ba8f38f996565dff5bb5fce2d8343092edbe658432d`.
- Stage 6C transfer streaming exact wheel tests pass 11/11; Documa focused tests pass
  17/17, full suite 353/353, and Ruff passes. Draining transfer alone dropped
  18.3381% and 17.0152%. Aggregate-only decorative mapping then reaches 61.5393%
  and 18.4775% reductions with identical text SHA/block/span counts; the global maximum
  remains 1.449143x PyMuPDF. Wheel SHA-256:
  `b5058b6d243ba4bef955fce3978972ab6240bf7bb752858a86785cfa90c91de7`.
- Stage 6C2-A event/collector tests pass 5/5 and Stage 12 contract passes 20/20.
  The 7-document/1,113-page private parity run matches all canonical SHA-256 values
  and serialized byte counts; 201.748765 pages/s and 674,770,944-byte core RSS do
  not regress the Stage 5 core baseline. Privacy-safe report SHA-256:
  `e39eb613c419aa671cfc5f0c61f7b0d6f842131ff566f4da97ecb4b918398dd5`.
- Stage 6C2-A exact wheel tests pass 11/11; wheel size is 1,072,142 bytes and SHA-256
  is `561d797fa9d4e2d93c137d6913d3d4e4527a27bc29df8d0a573441d49fca73db`.
  Node WASM passes 8+2, Documa focused/full passes 17/17 and 353/353, formatting,
  native/wasm32 Clippy `-D warnings`, and full workspace tests/doctests all pass.
- Stage 6C2-B producer tests pass 2/2; configured AI/Taiwan ContentOrder/Layout/Auto
  private contracts pass. Frozen 7-document/1,113-page Layout IR hashes and bytes remain
  exact. Core throughput is 212.467582 pages/s and RSS is 444,100,608 bytes (-34.1850%
  versus 6C2-A). Report SHA-256:
  `8da9a29de5cea7fa07997133076537f4669ae157ef3ef76881904ff0e9a7d4f2`.
- Stage 6C2-B exact wheel 1,071,719 bytes SHA-256 is
  `358e116ae8ede8c7d2dba8e79400adce374842c2daa0bcb5319fbdf647e7e74c`;
  wheel tests pass 11/11, Node WASM 8+2, Documa focused/full 17/17 and 353/353,
  format, native/wasm32 Clippy, workspace tests, and doctests all pass.
- Interim one-run complete Documa shadow: Rust 27.818060 pages/s versus PyMuPDF
  7.461973 pages/s (3.727976x), but Rust RSS 884,908,032 bytes is still 1.454378x.
  F1 remains 0.960813/0.951281. Report SHA-256:
  `7f1d17be13124b0e111330c123388b248ab0359c573c18308394f0db80084e26`.- Stage 6C2-C/D combined private run: 7 documents / 1,113 pages, exact canonical
  SHA/bytes/counts versus 6C2-B, 182.426213 pages/s, 434,147,328-byte core RSS,
  427,050,863 serialized bytes, deterministic/schema/privacy 7/7. Report SHA-256:
  `149f92aaf43a4806a36b76e373fc6dcb9070c08c1f43f289195bcdcbc9f9bcfb`.
- Stage 6C2-C/D focused reading/tagged/table/image/figure/navigation tests pass
  55/55; event tests pass 6/6; Stage 12 contract passes 23/23. Formatting,
  native/wasm Clippy, full workspace/private tests and doctests, Node WASM 8+2,
  and exact wheel tests 11/11 pass.
- Final C/D wheel: 1,108,719 bytes, SHA-256
  `8bfde5151edae46e828aaa27d125073b1e4d94915241da5b7fc874586a6036e1`.
  Documa doctor is 8/8 with fixture readiness 18/18; focused/full tests pass
  17/17 and 353/353; full Ruff passes. PyMuPDF remains default and renderer.

- Stage 6C2-E native lazy core run: 7 PDFs / 1,113 pages, exact Stage 6C2-C/D
  canonical SHA/bytes/counts, deterministic/schema/privacy 7/7, 161.881041 pages/s,
  441,270,272-byte core RSS, 427,050,863 serialized bytes. Report SHA-256:
  `9c3d666e4561e2dc7bf8793b6ffed21a06663a7c9c53663215372adcbb16f776`.
- Full native-events Documa shadow: Rust 20.071995 pages/s versus PyMuPDF 5.451006
  (3.682255x), unchanged F1 0.960813/0.951281, but 946,515,968-byte Rust RSS
  versus 609,292,288 (1.553468x). Report SHA-256:
  `2290936cbe21e4da0d1301b062dab08ffdde481b7d845594b768827afe630c21`.
- Per-page finalization draining lowered the isolated 423-page AI Index Rust probe
  to 900,263,936 bytes, still above the 1.2x gate; Stage 6D must compact mapped
  Documa metadata/provenance. Default-provider cutover remains forbidden.
- Stage 6C2-E event tests pass 8/8 and contract passes 24/24. Final exact wheel tests
  pass 11/11; wheel size 1,100,990 bytes, SHA-256
  `5ac374d01ec0bfeaea88b1595d8f720237a1adb94d0ae7e5fc7169fa48bf3d61`.
  Node WASM passes 8+2; Documa focused/full pass 17/17 and 353/353; full Ruff,
  formatting, native/wasm Clippy, workspace private tests, and doctests pass.
- Stage 6D final shadow: 7 PDFs / 1,113 pages, one warm-up plus three measured runs;
  Rust 34.704637 pages/s versus PyMuPDF 5.976338 (5.807007x), 646,643,712-byte
  Rust maximum RSS versus 612,139,008 (1.056367x), and deterministic 7/7 for both.
  Rust text SHA and block/span/semantic counts remain exact to Stage 6C2-E. Report
  SHA-256: `245966517805ae6d4689355307c7bd12e1f8675b41b87476bc600759a10ac44d`.
- The Stage 6D AI Index lifecycle profile separates a 356,667,392-byte parse peak
  from a 564,396,032-byte canonical-serialization peak. Canonical IR falls 37.53%
  and encoded metadata 67.38%; profile SHA-256:
  `14f4b276fca4583e3784850c43cc6889513d7fc34fb07ffb67f06a22182c054b`.
- Documa focused tests pass 18/18, full suite 354/354, Ruff passes, and doctor is
  8/8 with fixture readiness 18/18. The memory gate passes; default cutover remains
  closed on text/order quality and missing private table/image gold.- Stage 6D repository gate: Stage 12 contract 25/25, `cargo fmt --all --check`,
  workspace all-target/all-feature Clippy `-D warnings`, and full workspace
  all-feature tests/doctests pass. The private core matrix was not configured in
  this shell; the separate three-run 7-document Documa shadow is the private gate.- Stage 7.0 freezes the quality-recovery technical/nontechnical/agent plan after a
  bounded official-source research gate (4 searches/2 reads). Plain-language check
  passes and Stage 12 contract passes 26/26. Parser behavior and provider default
  are unchanged; Stage 7.1 must reproduce Stage 6D aggregate F1 page by page.
- Stage 7.1 process-isolated page localization covers 7 documents / 1,113 pages and
  exactly reproduces every Stage 6D per-case/global character and bigram score within
  1e-12. Aggregate character precision/recall/F1 are 0.999313/0.925169/0.960813;
  438 pages miss character F1 0.995 and 593 miss bigram F1 0.99. The report contains
  no text, character keys, private paths, URLs, complete counters, or private IR;
  report SHA-256 is b365b13e643f2fd32c9e386e219c07e11bca23f0bf631958fa3e0a527f266f4e.
  The self-test, privacy audit, focused contract 27/27, formatting, workspace all-target/
  all-feature Clippy -D warnings, full workspace tests, and doctests pass. Stage 7.2
  starts with missing extraction/font mapping before reading-order changes.
- Stage 7.2 corrected the oracle boundary: Stage 7.1 complete-adapter quality included
  PyMuPDF `find_tables()` rewriting and was not raw parser text truth. The process-isolated
  raw comparator covers 7 documents / 1,113 pages with aggregate character F1 0.998954
  and bigram F1 0.996075; all per-document character F1 values exceed 0.995, and Rust
  page-root/source-node multisets match 1,113/1,113. One-pass calibration timing is
  113.0875 pages/s Rust versus 97.7086 PyMuPDF raw. Report SHA-256 is
  5281d646379a5c38686b93a510c4af84ebe96d9e4419dd338a13f5c547c14f87.
  Self-test, privacy audit, focused contract 28/28, formatting, workspace all-target/
  all-feature Clippy -D warnings, full workspace tests, and doctests pass. No parser or
  provider behavior changed; Stage 7.3 now owns reviewed human reading-order gold.
- Stage 7.3 adds a privacy-denying validator/scorer for precedence pairs, complete
  main-flow/artifact membership, duplicate/unknown/cycle rejection, two independent
  reviewers, and coded adjudication. Public perfect scoring is 1.0/1.0/1.0 for
  pairwise/main-flow/artifact; the inverted fixture is 0.333333/0.888889/0.0 and FAIL.
  Public report SHAs are 96320261853af64d774689e820706f270298c0e396841a45b91278bbef469da3
  and 12abdf537f8d7fa4777238c3647fff56719afd148deba0d0efc528435262308d.
  The unconfigured private template returns BLOCKED (`human_order_gold_unconfigured`),
  report SHA d29c12e4dd7d35f51367d57728813ab54a561dfd6d68c273d9a052055992e8e1.
- The final private Stage 7.3 v6 packet deterministically covers 7 documents / 28
  pages with 28 clean PNGs, 28 box-only overlays, a launcher, two locked reviewer
  workspaces, and one adjudication workspace. Desktop/tablet/mobile visual QA,
  click-order/artifact/undo interaction, reviewer-export isolation, and invalid
  import rejection pass. JSON/HTML metadata contains no extracted text, PDF name,
  absolute path, or file URL. Packet index SHA is
  61467be3ee071c86673412fe36366b1362819416bc2dc2b61ff264fc5855cd53;
  draft manifest SHA is 14071377750d21073eea5351361fb95da6355fe9546b5a19e19f66b2c97e746b.
  Draft validation remains BLOCKED (`human_order_review_incomplete`) for 28 pages,
  report SHA ea779eea674ff35f4358496490023de90f21511ee503948b9994cfe21f1e9b41.
- Synthetic browser QA completed all 28 pages with one coded disagreement and
  exported manifest SHA a2f9941957da66f28a622277b7dd7da5d91b89acc8c76d56899581ce1d600ed6;
  Python authority validation returned VALID, report SHA
  30ca6225830dffe8a3282061b394e285e0431d7e553b467f43c53a16f79380b9.
  Synthetic files/screenshots, browser profiles, and obsolete v3-v5 packets were
  removed after evidence capture; v6 remains private under `target/`.
- Order-gold/review-packet self-tests, Ruff check/format, UI syntax, frontend audit
  (8 PASS/1 unrelated documentation warning/0 FAIL), focused contract 30/30,
  `cargo fmt --all --check`, workspace all-target/all-feature Clippy `-D warnings`,
  full workspace all-feature tests, and doctests pass. No ordering rule changed;
  Stage 7.4 must not begin before two real human reviewers complete private gold.
- Stage 7.3A replaced baseline-thin horizontal text extents with effective Tm/CTM
  geometry and bounded font vertical metrics. On the same 7-document/28-page sample,
  visible nodes fell 1,495 -> 993, median bbox height rose 0.126263% -> 1.202020%
  of page height, boxes below 0.5% fell 1,313 -> 5, and display-overflow boxes fell
  11 -> 0 after presentation-only clipping. Packet-index SHA-256 is
  0a5b0f7b5b1e9d1cba7436d43f8b5cc2593a483303a54de58802277b966d1873.
- The exact Stage 7.3A wheel reran all 1,113 pages: raw character F1 stayed exactly
  0.9989543801655596 and one-pass Rust layout-source throughput was 130.092516
  pages/s (no-regression evidence, not a controlled speedup claim). Report SHA-256
  is 8e788ab410b87396a288dfcf545df7fddfda2e718668d2ec3b13b4e6c40a4ee6.
  Packet/quality self-tests, Ruff, focused contract 31/31, layout IR 5/5, formatting,
  workspace all-target/all-feature Clippy -D warnings, full workspace all-feature
  tests, and doctests pass. Stage 7.3B may begin; Stage 7.3C/7.4 remain forbidden.
- Stage 7.3B freezes gold/candidate schema v2 around node-to-block membership, block
  role, unspecified internal order, and main-flow block precedence. Reviewer agreement
  canonicalizes role/membership so reviewer-local block IDs do not affect equality.
  Perfect public scoring is block macro/cross-node/main-flow/artifact 1.0; the inverted
  case is 0.25/0.266667/0.888889/0.0 and FAIL. Report SHAs are
  918006db44dc4b4149182ba732edab7fb5e63b8a0373e93c7ada2ebe50da22b0 and
  b942589c9381f2ac1d203f7bb2a78a2146a76ff8d98745fc28f24c893cb5756e.
  The unconfigured private v2 template remains BLOCKED, report SHA
  38cab2bb8e85fe26ae87dec6e004de8944378c4da4cca2bff2dcd37595ef3216.
  Self-tests, Ruff, focused contract 32/32, formatting, workspace all-target/all-feature
  Clippy -D warnings, full workspace all-feature tests, and doctests pass. Stage 7.3C
  may begin; no private human gold exists and Stage 7.4 remains forbidden.
- Stage 7.3C ships the blind schema-v2 brush workbench in private packet v10: 7 documents,
  28 pages, and 993 visible nodes. The builder default is pinned to the verified
  `stage7c-bbox-python-exact` wheel so old Stage 6 geometry cannot silently return. Real
  pointer brush/erase/split/merge/undo, 390x844 and
  820x900 responsive layouts, 172-node high-density page, B/E/P keys, 225% zoom clamp,
  and reload-safe `b0001` to `b0002` continuation pass with zero console errors. Draft
  authority remains BLOCKED with SHA 682ffc9ce1e1dd31c4509090b18e782a1098335daf2caa61a599948e452e19d3;
  synthetic interchange validation is VALID with SHA abf429fc6a029b8dae3f50c64c9e119b9f0a9f0f1e3e44a6b01b82b709048545.
- Stage 7.3D adds a separate timed-pilot validator and privacy-safe unconfigured template.
  Public READY/malformed self-tests pass; the current private draft remains BLOCKED with
  reason `human_order_pilot_unconfigured`. Report SHA is
  a5ac0eeb099be5dfd8648a7f7350db50ed9a49f167eb8abbd2318fa644846bc6. Packet/gold/pilot
  self-tests, Ruff, focused contract 33/33, formatting, workspace all-target/all-feature
  Clippy -D warnings, full workspace all-feature tests, and doctests pass. Stage 7.4
  remains forbidden until two real reviewers and adjudication produce READY artifacts.
- Release toolchain gate: `cargo fmt --all --check`, workspace all-target check, Clippy with
  `-D warnings`, full workspace tests, doctest, wasm32 check/Clippy, and Rust 1.88 all-target check passed.
- Full workspace test ran with both private env vars; core private contract 1/1 and CLI private matrix
  1/1 actually executed. AI Index: PDF 1.7, 45,151/45,151 objects, 423 pages. Taiwan PDF:
  PDF 1.4, 1,317/1,317 objects, 15 pages.
- Stage 11.6 validation matrix: FID-01～REG-01 PASS; synthetic, exact-boundary, truncated-prefix,
  malformed nesting, warning amplification, four-front-end parity, private corpus, benchmark,
  security, MSRV, deny, and audit all have direct evidence.
- Auto required/forbidden fragments passed for `Artificial Intelligence Index Report 2026` and
  `台灣政府動畫宣導影片`; known per-letter/per-CJK spacing artifacts are absent in Auto.
- Python 0.2.0 wheel replaced installed 0.1.0 in `.stage11-python`; with external pytest plugin
  autoload disabled, 5/5 tests passed in 0.01s and version tuple is `('0.2.0', 'stage-11')`.
- Browser WASM: Node wasm-bindgen 4/4; generated web-target module loaded directly in Node and
  extracted `Hello PDF text`; declarations expose `versionInfo`, `inspect`, `extractText`, `extract`,
  `extractWithOptions`, and `extractImages`.
- Public README CLI regression verifies `text-minimal.pdf` SHA and Auto text; `rustdoc --test README.md`
  compiles all three Rust examples. Core crate doctest 1/1 passed.
- Final 60-second Auto-enabled `parse_document` fuzz: 1,162,516 runs, coverage 674, features 2,368,
  0 crash, slowest unit 0 seconds, peak RSS 602 MiB. MSVC `.lib/.exp` linker stdout is non-fatal.
- Final fixed 2,000-glyph benchmark (50 iterations): ContentOrder 4,146,392 ns/iteration,
  legacy Layout 4,465,646, Auto 4,291,616; Auto/legacy 0.9610 (-3.90%). Timing is evidence,
  not the sole gate.
- cargo-deny: advisories/bans/licenses/sources OK; warnings only for unused BSD-2-Clause/ISC
  allowances and expected `syn` 2/3 duplicate. cargo-audit scanned 100 dependencies, 0 vulnerability.
- Dependency tree/manifests contain no forbidden PDF-aware parser. Core retains
  `#![forbid(unsafe_code)]`, no other unsafe, global cache, hidden worker thread, tokio, or rayon.
- Public fixture hashes pass, including `text-minimal.pdf` SHA-256
  `409a5b27adf14ba622a130c73826872155f41c8c3b45176e011a92cbf2056bdb`.
- Technical-documentation structural audit: README, architecture, compatibility, errors, security,
  release, text-fidelity, migration guide, Stage 11 matrix, fixture README, private how-to, and final
  DoD evidence all scored 100/100. Local-link audit covered 19 Markdown files with 0 broken links.
- Release artifacts:
  - `target/package/pdf-core-0.2.0.crate`: 72,298 bytes, SHA-256
    `770a84fe0a7e7acac6dabddee2f878c8c8bca530aa3b01ce55bfefcf1d121d95`; Cargo verify passed.
  - `.stage11-dist/rust-pdf-cli-0.2.0-windows-x86_64.zip`: 793,624 bytes, SHA-256
    `fedb181174ae867f87716183fd4a1ad999c41947252518939b5a632e7b1c990d`.
  - `target/stage11-wheels/rust_pdf_parser-0.2.0-cp310-cp310-win_amd64.whl`: 705,071 bytes,
    SHA-256 `47720f3df384fbd39c57f3c2a0834a68acfee56d774b69c6354cbadd3d0b9d7a`.
  - `bindings/wasm/pkg-stage11-rc/pdf_wasm_bg.wasm`: 967,315 bytes, SHA-256
    `4d232cda576c8758addb7d25372bf8894dc8bd0d5138d0b9b405dcb8652545be`.

## LANDMINES
- paddleocr_rs_onnx 0.2.7 references every ORT execution-provider type even in
  CPU mode. The wrapper therefore needs the corresponding Cargo features to compile;
  ORT is configured with load-dynamic so unsupported cross-platform provider
  symbols are not linked. Runtime inference requires a compatible ONNX Runtime
  shared library discoverable by the loader or explicitly named by
  ORT_DYLIB_PATH.

- Interactive Windows PTY input can encode non-ASCII patch text incorrectly. Keep patch payloads
  ASCII-only or use a verified UTF-8-safe path, and compile/include-check new text artifacts.
- Stage 12 reports and review packets are private-corpus-derived evidence under `target/`; never commit IR, rendered pages, manifests, or review HTML.
- Stage 1B full Layout IR is intentionally verbose: the frozen corpus serializes to about
  271.9 MB. Avoid retaining both JSON bytes and decoded object graphs for large documents.
- Direct updates with `apply_patch` can fail under the Windows restricted-token sandbox.
  Adding a `.next` file and moving it after absolute-path validation is the reliable fallback.
- `rust-toolchain.toml` pins 1.96, while 1.88 is the verified MSRV.
- `wasm-pack` may require elevated access to its user-level wasm-bindgen cache.
- Headless ChromeDriver 151 returned HTTP 404 while creating the test window; the same DOM-free
  wasm-bindgen tests pass with the Node runner.
- cargo-deny/audit require elevated access to lock or update the user-level RustSec database.
- wasm-pack's bundled wasm-opt is disabled because it could not validate current bulk-memory
  output; the produced browser package itself was built and inspected successfully.
- The host Python environment auto-loads an external pytest plugin that can hang collection; release
  wheel tests must set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. The extension itself imports and tests normally.
- The `.stage11-python` pip wrapper can print install success but leave parent/child pip
  processes alive. Final Stage 2 wheel validation unpacked the wheel under `target/` and
  set `PYTHONPATH`, avoiding a false hang and proving the exact artifact under test.
- `pdf-cli` declares `pdf-core` with both path and version. A CLI crate can only complete Cargo registry
  verification after core 0.2.0 is published; the Stage 11 RC deliverable is the verified core crate plus
  standalone CLI archive. Do not misreport an unpublished registry dependency as a package failure.
- `C:\tmp` was not writable in this sandbox despite being nominally allowed.
- Generated `.stage11-*`, wasm package, target, fuzz corpus, and fuzz target paths are ignored.
- Private corpus overrides are `RUST_PDF_REAL_AI_INDEX` and `RUST_PDF_REAL_TAIWAN`; setting only
  one makes the configured corpus intentionally fail as incomplete.
- Stage 4 benchmark memory sampling uses `psutil`; the report records absolute RSS,
  but Stage 3 did not record comparable RSS, so do not claim the 1.2x ratio gate.
- Stage 5 full Layout IR is 427,050,863 serialized bytes across the frozen corpus,
  29.0143% above Stage 4. Stage 6 must not duplicate entire-document JSON and Python
  graphs in memory when a page-level or streaming contract can be used.
- Retaining parsed operations for an entire document restored speed but raised AI
  Index peak RSS to about 1.20 GB. The accepted design shares operations per page
  and releases them immediately after vector collection.

- Native page production plus Stage 6D compact mapping passes complete-adapter memory
  at 1.056367x PyMuPDF. Do not misread this as cutover approval: character/tagged-order
  quality and labeled table/image gates still fail or lack evidence.
- `DocumentStart.capabilities` are provisional; content-derived capabilities,
  warnings, navigation summaries, quality, timings, and furniture patches are final
  only after iteration reaches `DocumentFinalize`.
- Native lazy vector recovery is fail-forward. An accepted page is immutable;
  malformed later optional vector content cannot trigger legacy global rollback.- Documa full pytest with plugin autoload disabled must explicitly load
  `pytest_datadir.plugin` and `pytest_regressions.plugin` for snapshot fixtures.

## HISTORY
- Direct-image OCR: added bounded candidate analysis, stable rejection reasons,
  raw/JPEG decoding, pixel-quad to LayoutSpace mapping, a replaceable OCR engine
  contract, RapidOCR-compatible det/rec ONNX sessions, CLI/Python native entry
  points, WASM candidate transfer, tests, and documentation.

- Stage 0: workspace policies, CI, package skeletons, fixtures, fuzz harnesses.
- Stage 1: bounded lexical/object parser, classic/incremental xref, lazy resolution, CLI.
- Stage 2: Flate and TIFF/PNG predictors with decompression budgets.
- Stage 3: xref streams, hybrid xref, and object streams.
- Stages 4–6: page/content traversal, fonts, ToUnicode, Unicode spans, layout, Forms.
- Stage 7: Python and browser WASM APIs.
- Stage 8: bounded Image XObject extraction and JPEG codec validation.
- Stage 9: fuzzing plus truncation, malformed-input, and limit-boundary regressions.
- Stage 10: package validation, compatibility/security/error/release docs, supply-chain audit,
  MSRV correction and proof, and final completion audit.
- Real-world regression pass: standard CMap dictionary delimiters, indirect font encodings,
  malformed UTF-16 ToUnicode entries, highly compressible xref/object streams, and contextual
  CLI validation diagnostics.
- Stage 11 spec planning: three extraction modes, script-aware text fidelity, `/ActualText`,
  document-wide decode accounting, bounded object-stream caching, and private-corpus rules.
- Stage 11.0: versioned private-corpus contract, verified real-file hashes, generated Latin/CJK
  layout baselines, explicit skip semantics, and executable hash-mismatch regression.
- Stage 11.1: V2 `content-order`/`layout`/`auto` contract, quality DTO, legacy-preserving
  Rust/CLI/Python/WASM adapters, stable `invalid_option`, and cross-interface regressions.
- Stage 11.2: positioned glyph/source ordinal model, simple and CID font widths, horizontal/vertical
  advance with text/Form matrices and `TJ`, bounded glyph vectors, and a dual legacy matrix that
  preserves both private-corpus baselines.
- Stage 11.3: deterministic direction grouping and line clustering, script-aware separators with
  provenance, bounded ambiguity fallback, CJK compatibility canonicalization in Auto text, and
  passing real-world AI/Taiwan title regressions without changing legacy baselines.
- Stage 11.4: bounded marked-content stack, direct/named/indirect property resolution, nested
  ActualText suppression, MCID retention, stable recovery warnings, and reviewed private-corpus
  baseline changes driven by authoritative replacements.
- Stage 11.5: xref-origin document-lifetime DecodeBudget, immediate Flate/predictor accounting,
  clone-shared synchronized runtime, deterministic bounded object-stream LRU with validated member
  ranges, opt-in CLI diagnostics, exact-boundary/security regressions, and AI full-object metrics.
- Stage 11.6: full synthetic/private/cross-interface matrix, invalid-vs-missing Unicode warning fix,
  ActualText prefix coverage, warning-amplification regressions, Auto-enabled fuzz, and fixed benchmark.
- Stage 11.7: 0.2.0/stage-11 versioning, four executable front-end examples, public fixture smoke,
  migration/reference/release docs, verified core crate, CLI archive, Python wheel, browser WASM,
  60-second fuzz, package hashes, and final completion audit.
- Stage 12 Stage 0: frozen 7-document private corpus contract, reproducible PyMuPDF/Documa/Rust
  baseline, deterministic result hashes, privacy-safe reporting, Go/No-Go metrics, explicit
  coordinate-system contract, and passing focused/format/Clippy/workspace gates.
- Stage 12 Stage 1A: canonical PdfUserSpace/LayoutSpace/DisplaySpace contract,
  bounded reversible PageGeometry, inherited/indirect page boxes and rotation,
  stable geometry diagnostics, CLI serialization, synthetic BBox/Quad/Form coverage,
  and zero-mismatch 1,113-page PyMuPDF parity with privacy-safe reporting.
- Stage 12 Stage 1B: schema-versioned Layout IR, normalized text geometry,
  explicit capabilities and four orders, bounded deterministic node/span construction,
  Rust/CLI/Python/WASM exposure, privacy-safe full-corpus schema/performance audit,
  and passing cross-front-end plus full workspace gates.
- Stage 12 Stage 2: marked-content metadata, bounded StructTreeRoot/RoleMap/ParentTree
  traversal, structure-derived tagged order, artifact preservation, stable diagnostics,
  per-page MCID and ParentTree indexing, four-front-end parity, and privacy-safe
  full-corpus tagged/performance evidence.
- Stage 12 Stage 3: bounded line clustering and XY-cut, paragraph/list grouping,
  strict page-number and repeated-furniture classification, independent source/tagged/
  inferred/main-flow orders, warned rotation/depth fallback, four-front-end parity,
  and privacy-safe tagged-order proxy/performance evidence.
- Stage 12 Stage 4A: bounded tagged Table/TR/TH/TD ancestry, RoleMap aliases,
  RowSpan/ColSpan and header Scope, deterministic rectangular placement, honest
  empty-cell geometry, source-node links, explicit table limits, and passing
  native/wasm/full-workspace gates.
- Stage 12 Stage 4B: bounded stroke-path/Form/CTM traversal, exactly-once
  LayoutSpace projection, fragmented-rule merge, closed-grid reconstruction,
  negative fixtures, and exact path/candidate limits.
- Stage 12 Stage 4C: conservative borderless row/alignment detection, numeric
  two-column evidence, furniture/artifact exclusion, ambiguity preservation, and
  false-positive/limit regressions.
- Stage 12 Stage 4D: tagged/vector fusion and conflict precedence, shared per-page
  operations, four-front-end nonempty table parity, CJK/multiline/rotation
  coverage, privacy-safe performance/memory reporting, and the final Stage 4 gate.
- Stage 12 Stage 5A: bounded painted Image XObject occurrences, exact unit-square
  Quad/BBox projection, stable resource/object/paint identity, recoverable optional
  geometry, four-front-end parity, and the full substage gate.
- Stage 12 Stage 5B: marked Figure/MCID/Alt/Artifact retention, StructTree Figure
  precedence, conservative caption anchors, ambiguity/table/furniture exclusion,
  unchanged node orders, four-front-end parity, and the full substage gate.
- Stage 12 Stage 5C: bounded Link annotations, URI/GoTo targets, old/name-tree
  destinations, outline preorder, unsupported-action non-execution, optional-data
  recovery, exact limits, and four-front-end parity.
- Stage 12 Stage 5D: privacy-safe full-corpus image/navigation benchmark, explicit
  Stage 4 speed/memory/size ratios, final Python/WASM artifacts, and the complete
  Stage 5 gate with default-provider cutover still closed.
- Stage 12 Stage 6A/6B: opt-in Documa Rust adapter, stable provider/errors,
  coordinate/order/table/image/navigation/provenance mapping, reading-order lock,
  complete-adapter private shadow benchmark, and explicit default-provider NO-GO.
- Stage 12 Stage 6C transfer: backward-compatible draining Python page stream,
  aggregate-only decorative Documa mapping with reversible opt-in,
  older-wheel fallback, exact page parity, Documa full regression, stress RSS reduction,
  and proof that native incremental page-production remains required.
- Stage 12 Stage 6C2-A: bounded event DTOs, owned page event iterator, validating
  compatibility collector, stable-ID finalization patches, exact synthetic/private
  byte parity, exact wheel/WASM/Documa regression, and explicit 6C2-B handoff without
  claiming native page production or opening default-provider cutover.
- Stage 12 Stage 6C2-B: shared-runtime page text producer, incremental source/limit
  state, immediate glyph/marked-content/operation release after PageLayout conversion,
  private all-mode and Layout byte parity, 34.1850% core RSS reduction, exact wheel/
  WASM/Documa gates, and evidence that complete PageLayout retention still blocks RSS.- Stage 12 Stage 6C2-C: compact tagged/navigation page indexes; page-local reading,
  tagged/table/image/figure/navigation semantics; legacy warning state and vector
  rollback; exact private byte parity; and full cross-front-end gates.
- Stage 12 Stage 6C2-D: compact repeated-furniture collector, original/final role
  metadata, real stable-ID event finalizations, provisional-page collector parity,
  exact private output, final wheel/Documa gates, and explicit lazy-producer handoff.

- Stage 12 Stage 6C2-E: owned fallible native event producer, first-page-before-later-
  error proof, cancellation safety, provisional/final capabilities, Python
  `native_events_v2`, per-page stable-ID finalization draining, Documa patch application,
  exact private parity, final cross-front-end gates, and explicit RSS/quality NO-GO.
- Stage 12 Stage 6D: default compact trace schema with inherited page/document context,
  reversible verbose metadata, unchanged citation/semantic parity, privacy-safe
  lifecycle profiling, three-run 1.056367x complete-adapter RSS pass, 5.807007x
  speedup, full Documa gates, and default cutover still closed on quality/gold.
- Stage 12 Stage 7.0: official-source content/logical/inferred-order research,
  technical/nontechnical/Codex-Claude quality recovery specs, executable 0.995/
  0.95/gold/privacy/rollback gates, plain-language validation, and Stage 7.1
  handoff without parser or default-provider changes.
- Stage 12 Stage 7.1: process-isolated temporary character/bigram counters,
  exact page alignment, Unicode category/script and warning-code localization,
  exact Stage 6D metric reproduction, privacy/cleanup enforcement, and evidence
  that missing extraction/font mapping precedes reading-order recovery.
- Stage 12 Stage 7.2: separated raw parser extraction from Documa table semantics,
  found PyMuPDF `find_tables()` contamination in the former character oracle,
  established 0.998954/0.996075 raw character/bigram F1, proved page-root/source-node
  completeness, and passed the text gate without a corpus-specific core heuristic.
- Stage 12 Stage 7.3B: block-level gold schema v2, ID-independent reviewer
  agreement, macro block-pair scorer, privacy/invalid fixtures, and explicit v1 rejection.
- Stage 12 Stage 7.3A: effective font-metric BBoxes, display-bound review overlays,
  private geometry/quality validation, and block-review re-gating.
- Stage 12 Stage 7.3: versioned human-order manifest, complete node classification,
  acyclic precedence validation, double-review/adjudication policy, exact public
  scorer fixtures, privacy denial, deterministic 7-document/28-page private packet,
  exactly-once LayoutSpace-to-DisplaySpace overlays, locked click-order/artifact
  reviewer workspaces, identity-checked import/merge, coded adjudication, responsive
  browser QA, and an explicit human-review BLOCKED gate that forbids Stage 7.4
  ordering changes without real human truth.
- Stage 12 Stage 7.3C: blind neutral block-brush workbench, schema-v2 reviewer isolation,
  responsive/pointer/keyboard browser QA, reload-safe monotonic block IDs, private v10 packet,
  and complete engineering gate while preserving the real-human BLOCKED state.
- Stage 12 Stage 7.3D: separate bounded timed-pilot log/validator, privacy-safe aggregate
  labor metrics, public malformed/exact self-tests, and explicit no-synthetic/no-cutover gate.

- Additive visual-reading graph: optional schema-v1 attention DTO, simultaneous semantic
  blocks, multiple focus candidates, bounded continue/skip/regression transitions, preserved
  skippable peripheral blocks, shared Rust/CLI/Python/WASM serialization, and explicit
  separation from the still-blocked Stage 7.4 human-order cutover.


### 2026-10-12 — Backport downstream PDF fixes to canonical source

- Synced graphics.rs, layout.rs, layout_ir.rs, reading_order.rs and related Form/geometry/reading-order regressions from Documa; resolved Differences references through the existing cycle/depth-bounded resolver. Added encoding_differences.rs. Nine implementation/test files changed; unrelated OCR source preserved.
- Source workspace: 236 passed / 1 manual benchmark ignored; cargo fmt and workspace all-target Clippy PASS. Source-built Python extension with current Documa: 81 integration tests passed; original PDF 19 pages, no warnings/replacement characters.
- File backups and hash receipt: SkillNow platformdirs diagnostics/upstream-sync-20261012/receipt.json. Exact before/after audit is maintained in SkillNow/docs/evidence; see docs/NATIVE_UPSTREAM_SYNC_20261012.md. No commit/tag/publication or installed package updates.

### 2026-10-12 — Priority engineering completion and Documa submission

- Implemented and validated all non-human-gold P0-P2 work; exact results and limitations are in docs/priority-implementation-2026-10-12.md and local target/priority-evidence. The public fixture/model manifests contain no private documents.
- Integrated the full core/OCR/CLI/Python/WASM workspace with the Documa package; synchronized src/rust_pdf wrappers, preserved existing Documa-only fixes and merged the remote CI retention changes without rewriting history. Topic commits use actual commit times and describe the historical work dates in messages.
- Documa-native rerun: formatting, all-target/all-feature Clippy and 264 Rust tests PASS (1 existing manual benchmark ignored). Full Documa source integration verification is recorded in Documa/docs/PDF_PRIORITY_INTEGRATION_20261012.md. Human quality acceptance and registry publication remain outside this submission.
