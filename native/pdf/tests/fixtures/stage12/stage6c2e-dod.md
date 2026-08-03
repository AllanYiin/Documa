# Stage 12 Stage 6C2-E Definition of Done

Status: Native lazy production complete; Stage 6C2 acceptance remains NO-GO

## Delivered contract

- `LayoutEventProducer` owns clone-shared parser state, compact tagged/navigation
  indexes, incremental quality/warning state, and one `TextPageProducer`. It emits
  fallible `DocumentStart`, one page at a time, then `DocumentFinalize`.
- Page content is decoded only when its event is requested. A two-page regression
  proves the first page is delivered before malformed second-page content fails.
- Consumer cancellation is ordinary Rust ownership drop: there is no worker,
  process, temporary file, or background task to leak.
- Content-derived capabilities and repeated furniture remain deterministic terminal
  patches. The complete collector applies them and preserves the existing
  `DocumentLayout` JSON contract.
- Python `native_events_v2` owns the Rust producer directly. It never constructs a
  complete `VecDeque<PageLayout>` or full Layout JSON string. Final page patches are
  drained individually as `draining_stable_id_patches_v1`.
- Documa maps provisional pages immediately, drains stable-ID finalizations before
  returning `DocumentIR`, and keeps PyMuPDF as the default and rendering/OCR path.

## Exactness and private evidence

- The frozen 7-document / 1,113-page core run is exact against Stage 6C2-C/D for
  every canonical SHA-256, serialized byte count, and semantic count. It remains
  deterministic, schema-safe, and privacy-safe.
- Core report: `target/stage12-stage6c2e-native-lazy/report.json`, SHA-256
  `9c3d666e4561e2dc7bf8793b6ffed21a06663a7c9c53663215372adcbb16f776`.
- One-run core measurement: 161.881041 pages/s, 441,270,272-byte peak RSS, and
  427,050,863 serialized bytes. Timing is evidence, not a quality waiver.
- Complete Documa output retains the Stage 6C2-B text SHA and block/span/count
  parity for all seven documents. Character/bigram F1 remains
  0.9608131914/0.9512812709.

## Performance decision

- The first full `native_events_v2` shadow measured Rust at 20.071995 pages/s versus
  PyMuPDF Documa at 5.451006 pages/s, a 3.682255x speedup.
- That full run measured 946,515,968-byte Rust peak RSS versus 609,292,288 bytes for
  PyMuPDF, or 1.553468x. Its privacy-safe report SHA-256 is
  `2290936cbe21e4da0d1301b062dab08ffdde481b7d845594b768827afe630c21`.
- Draining finalizations individually and removing the Documa all-node patch index
  lowered the isolated 423-page AI Index Rust probe to 900,263,936 bytes, but this
  still exceeds the 1.2x gate. Native laziness is complete; mapped IR amplification
  is now the dominant memory problem.

## Validation

- Native event tests pass 8/8, including delayed patches, later-page failure, and
  cancellation. The complete pdf-core suite and doctests pass.
- Exact CPython 3.10 wheel tests pass 11/11. Final wheel:
  1,100,990 bytes, SHA-256
  `5ac374d01ec0bfeaea88b1595d8f720237a1adb94d0ae7e5fc7169fa48bf3d61`.
- Documa Rust adapter/reading-order focused tests pass 17/17; the full suite passes
  353/353 and full Ruff passes.
- Formatting and native Clippy with warnings denied pass. The final workspace/WASM
  gate is recorded in the repository DEVNOTE.

## Recovery boundary

A later non-limit vector-path failure cannot retract an already accepted page.
Native streaming therefore uses fail-forward optional-vector recovery: prior page
payloads remain valid, the failing page uses empty optional vector data, later
vector collection is disabled, and `vector_path_invalid` is finalized. Limit errors
still terminate immediately. Complete output remains exact for all frozen valid
inputs; malformed multi-page rollback is intentionally not promised by the lazy API.

## Gate and next stage

Default-provider cutover remains forbidden. The 0.995 character-F1 target, 1.2x
complete-adapter RSS target, tagged-order target, and private table/image gold gates
are not satisfied. The next development stage is Stage 6D: compact Documa Rust
metadata/provenance without losing citation traceability, measure object-lifetime
peaks separately from canonical serialization, and continue reading-order/text
quality work before any default cutover.