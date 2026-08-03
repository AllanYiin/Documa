# Stage 12 Stage 7.3 Definition of Done

Status: Public tooling and private annotation workbench complete; private human gold BLOCKED; Stage 7.4 forbidden

## Implemented

- `tools/stage12_order_gold.py` validates schema version, privacy denylist,
  document/page/node identity, complete main-flow/artifact classification,
  precedence references, duplicates, cycles, two independent reviewers, and
  adjudication reason codes.
- The scorer reports pairwise precedence accuracy, main-flow
  precision/recall/F1, artifact-role accuracy, per-page reviewer agreement, and
  an explicit PASS/FAIL status.
- Public fixtures cover single column, two columns, spanning heading, sidebar,
  caption, list, table, page furniture, page number, and vertical/rotated policy
  labels without embedding document text.
- The private example manifest is intentionally unconfigured and returns
  `BLOCKED` with reason `human_order_gold_unconfigured`, never PASS.
- `tools/stage12_order_review_packet.py` deterministically selects 28 pages from
  all 7 private documents and emits clean renders, box-only overlays, a launcher,
  two locked independent reviewer workspaces, and a separate adjudication
  workspace.
- `tools/stage12_order_review_ui.html` supports click-order annotation, artifact
  roles, ordered-flow editing, undo, page completion, local recovery, reviewer-
  only export, identity-checked import/merge, coded adjudication, keyboard access,
  responsive layouts, and a non-persistent synthetic browser-QA mode.
- Imported labels are rejected before merge for unknown/duplicate nodes,
  overlapping flow/artifacts, invalid roles, malformed/duplicate/self-reference
  pairs, precedence cycles, invalid reason codes, identity mismatch, or conflict.
- LayoutSpace boxes are transformed by the frozen per-page `layout_to_display`
  matrix exactly once before PNG scaling. Private page images remain under
  `target/`, are marked `must_not_commit`, and no extracted text enters metadata.

## Public exact evidence

- Perfect candidate: pairwise `1.0`, main-flow F1 `1.0`, artifact-role accuracy
  `1.0`, status PASS.
- Inverted/incomplete candidate: pairwise `0.3333333333333333`, main-flow F1
  `0.888888888888889`, artifact-role accuracy `0.0`, status FAIL.
- Perfect report SHA-256:
  `96320261853af64d774689e820706f270298c0e396841a45b91278bbef469da3`
- Inverted report SHA-256:
  `12abdf537f8d7fa4777238c3647fff56719afd148deba0d0efc528435262308d`
- Private blocked report SHA-256:
  `d29c12e4dd7d35f51367d57728813ab54a561dfd6d68c273d9a052055992e8e1`

## Private review-packet evidence

- Final local packet: `target/stage12-stage7c-order-review-private-v6`.
- 7 documents, 28 selected pages, 28 clean PNGs, and 28 box-only overlay PNGs.
- Desktop 1440x1000, tablet 820x1180, and mobile 390x844 passed visual review;
  the page canvas remains primary, the header wraps without clipping, and the
  inspector moves below the canvas on mobile.
- Real browser interaction passed: locked Reviewer A identity, click-to-flow,
  page-number artifact assignment, Ctrl+Z, reviewer-only export isolation, and
  immediate rejection of an imported unknown node.
- Synthetic end-to-end browser state reached `status=complete`, 7 documents, 28
  pages, both independent reviews complete, and coded disagreement adjudication.
  The exported manifest then passed the Python authority validator as `VALID`.
- Ephemeral synthetic manifest SHA-256:
  `a2f9941957da66f28a622277b7dd7da5d91b89acc8c76d56899581ce1d600ed6`
- Ephemeral synthetic validation report SHA-256:
  `30ca6225830dffe8a3282061b394e285e0431d7e553b467f43c53a16f79380b9`
- Synthetic files/screenshots and browser profiles were removed after validation;
  they were QA evidence, never human gold.
- Draft validation status: `BLOCKED`, reason
  `human_order_review_incomplete`, review pages `28`, cutover `false`.
- Draft validation report SHA-256:
  `ea779eea674ff35f4358496490023de90f21511ee503948b9994cfe21f1e9b41`
- Draft manifest SHA-256:
  `14071377750d21073eea5351361fb95da6355fe9546b5a19e19f66b2c97e746b`
- Packet index SHA-256:
  `61467be3ee071c86673412fe36366b1362819416bc2dc2b61ff264fc5855cd53`
- HTML/JSON metadata privacy audit found no extracted phrases, PDF names,
  absolute source paths, or `file://` URLs; privacy flags are false/false and
  `must_not_commit=true`.

## Validation coverage

- Self-test rejects duplicate node IDs, precedence cycles, incomplete node
  coverage, privacy-forbidden content fields, and reviewer disagreement without
  an adjudication reason code.
- A candidate JSON used as a manifest is rejected because required gold
  ownership/privacy fields are absent.
- Public reviewer labels agree exactly; private reviewer agreement is not
  claimed because no private human labels exist.
- Frontend principle audit with required workbench IA: 8 PASS, 1 documentation-
  structure warning outside this Stage 7.3 UI, 0 FAIL; anti-pattern scan PASS.

## Gate decision

- No Rust ordering rule, parser output, Documa adapter, or provider default was
  changed.
- The engineering side of Stage 7.3 is complete. The human-order gate remains
  genuinely BLOCKED until at least two real human reviewers complete and
  adjudicate the private manifest.
- Tagged-order proxy `0.940546` remains diagnostic evidence only, not human
  truth. Default-provider cutover remains forbidden.
- Stage 7.4 remains forbidden until the private manifest validates as READY.

## Repository validation

- Order-gold and review-packet Python self-tests: PASS; Ruff check and format
  check: PASS; UI JavaScript syntax: PASS.
- Public perfect/inverted scoring, unconfigured-private BLOCKED, and
  review-incomplete BLOCKED behavior: PASS with the exact SHAs above.
- Browser synthetic complete manifest and Python cross-validation: PASS.
- Stage 12 focused contract: 30/30 PASS.
- `cargo fmt --all --check`: PASS.
- Workspace all-target/all-feature Clippy with `-D warnings`: PASS.
- Full workspace all-feature tests and doctests: PASS.
