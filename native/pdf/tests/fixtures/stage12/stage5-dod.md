# Stage 12 Stage 5 Definition of Done Evidence

Status: PASS (2026-07-29)

## Delivered contract

- `pdf-core` owns painted image occurrences, Figure/Caption association, Link
  annotations, destinations, outlines, geometry, recovery, stable warnings, and
  resource limits. Bindings only serialize the shared Layout IR.
- Every public image/link geometry uses `layout_unrotated_top_left`: CropBox
  relative, UserUnit scaled, x right, y down, and page Rotate unapplied.
- Image unit-square corner identity survives q/Q/cm and nested Form matrices before
  exactly one PDF-to-Layout projection; repeated paints retain distinct ordinals.
- Author Figure/Caption/Alt/Artifact evidence has precedence. Conservative geometry
  may add a caption anchor without deleting or rewriting semantic nodes or orders.
- URI/GoTo, named destinations, and outline targets are additive metadata.
  JavaScript, Launch, embedded-file execution, and unknown actions are never run.
- Optional malformed placement/navigation data warns and preserves other content;
  all input-derived traversal and collection sizes remain bounded.

## Synthetic and interface validation

```text
cargo test -p pdf-core --test stage12_image_placements
5 passed; 0 failed

cargo test -p pdf-core --test stage12_figure_flow
5 passed; 0 failed

cargo test -p pdf-core --test stage12_navigation
3 passed; 0 failed

cargo test -p pdf-core --test stage12_contract
18/18 passed

cargo test -p pdf-cli --test layout
5 passed; 0 failed

exact built Python wheel
7 passed; 0 failed

wasm-pack test --node bindings/wasm
8 passed; 0 failed; web suite 2 passed
```

Coverage includes direct and nested Form image paints, exact transforms, repeated
resource use, marked Figure/Caption/Alt/Artifact, caption ambiguity and exclusion,
Link rectangles/quads, URI/GoTo, old and name-tree destinations, outline preorder,
unsupported-action non-execution, malformed recovery, and exact/one-short limits.

## Formal private-corpus benchmark

`tools/stage12_image_navigation_benchmark.py` ran one warm-up and three measured
release-CLI runs over 7 PDFs / 1,113 pages. The report stores no text, Alt text,
URLs, image bytes, node arrays, private Layout IR, or other extracted content.

- throughput: 195.707651 pages/s;
- speedup versus frozen complete Documa adapter: 26.930568x;
- throughput ratio versus Stage 4: 0.951276 (4.8724% feature cost);
- sum of document median durations: 5.687054 seconds;
- maximum sampled process RSS: 675,053,568 bytes;
- peak RSS ratio versus Stage 4: 1.000650x;
- serialized bytes for one run of every document: 427,050,863;
- serialized size ratio versus Stage 4: 1.290143x;
- image placements / unique image objects: 76,336 / 2,068;
- tagged figures / caption links: 169 / 48;
- links: 3,468 (URI 1,718; GoTo 1,750);
- named destinations / outline items: 1,393 / 136;
- unsupported navigation targets: 0;
- all 7 groups byte-deterministic; all schema and privacy audits passed;
- report SHA-256:
  `634695ce886186379ef5efabbfc6900773a59fa64b7fe21ab7a3d753d59c4fef`.

The 29.0143% serialized-size increase is accepted for the parser contract but is
an explicit Stage 6 integration constraint: Documa should consume pages or a
streamed representation instead of retaining both full JSON bytes and the decoded
Python object graph for a large document.

## Release-candidate artifacts

- Python CPython 3.10 wheel: 1,026,878 bytes, SHA-256
  `290a07aab92b1cdcadbf7693b87d8b2490338840e607546d72e712cc8af55cca`;
- browser WASM: 1,410,238 bytes, SHA-256
  `8fdd9ba4bb217486df9eb1d81feec7eca8277d04837e5e4e5580f3c29dc18f88`.

## Stage gate

```text
cargo fmt --all --check
PASS

cargo clippy --workspace --all-targets --all-features -- -D warnings
PASS

cargo clippy -p pdf-core -p pdf-wasm --target wasm32-unknown-unknown \
  --all-targets --all-features -- -D warnings
PASS

cargo test --workspace --all-features
PASS
```

## Gate decision

Stage 5 is complete and Stage 6 Documa shadow-adapter work may begin.
Default-provider cutover remains forbidden: normalized character F1 has not met
0.995, the tagged reading-order proxy remains 0.940546 below 0.95, and private
table ground truth is absent. Public version metadata intentionally remains
`0.2.0` / `stage-11` until the later release gate.