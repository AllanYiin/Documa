# Stage 12 Stage 7.3C Blind Brush Workbench Definition of Done

Status: Stage 7.3C engineering complete; Stage 7.3D pilot may begin; Stage 7.4 remains forbidden

## Frozen interaction and data contract

- The primary human action is one brush stroke per perceived reading block, not one click per parser BBox.
- Persisted schema version 2 stores `blocks`, `member_node_ids`, human `role`,
  `internal_order=unspecified`, and `block_precedence_pairs`; brush geometry is never truth.
- Brush, erase, split, merge, main-flow reorder, role correction, unassign, one-transaction
  undo, fit, and bounded 65%-225% zoom are implemented.
- Block IDs are page-local, monotonic, zero-padded, and recovered from persisted IDs with
  `/^b(\d+)$/`; undo does not reuse an allocated ID.
- Reviewer A and Reviewer B remain locked and independent. Reviewer-only export removes
  the other reviewer and adjudication. Adjudication accepts both files only after identity
  and schema validation and requires reason codes for disagreements.

## Blindness and privacy

- Final private packet: `target/stage12-stage7c-order-review-private-v10-brush`.
- The final private packet contains 7 documents, 28 selected pages, and 993 visible nodes.
- Interactive node payload keys are exactly `id` and `percent_box`; parser role, inferred
  order, artifact status, confidence, text, feature codes, and quality scores are absent.
- Neutral overlays are shown before human assignment. Human colors and badges appear only
  after a reviewer creates a block.
- HTML/JSON/Markdown privacy search found no absolute drive path, `file://` URL, source PDF
  filename, extracted title phrase, feature code, or parser probability.
- Private renderings and manifests remain under `target/`, `must_not_commit=true`,
  `private_corpus=true`, and `redistributable=false`.

## Browser QA

- Real pointer interactions passed for brush, erase, split, merge, and undo; a stroke is
  committed as one undo transaction. Zoom clamps at 225% and Fit restores 100%.
- Persistence regression passed: create `b0001`, reload the locked reviewer workspace, then
  create another block; the recovered ID is `b0002`, not a duplicate or reused ordinal.
- Keyboard B/E/P switched brush, erase, and pan while keeping correct `aria-pressed` state.
- Responsive checks passed at 390x844 and 820x900: the workbench becomes one column,
  the inspector becomes static below the canvas, and document-level horizontal overflow is zero.
- The desktop layout returns to a two-column `866.667px 350px` workbench at 1280x720.
- The high-density AI Index page 356 rendered 172 visible nodes without console errors.
- Reviewer and synthetic-QA tabs both reported zero browser console errors.

## Machine authority and hashes

- Blank draft authority result: `BLOCKED`, reason `human_order_review_incomplete`,
  `review_pages=28`, cutover false. Draft validation SHA-256:
  `682ffc9ce1e1dd31c4509090b18e782a1098335daf2caa61a599948e452e19d3`.
- Synthetic browser-only QA reached a complete schema-v2 state and passed the Python
  authority validator as `VALID`. Synthetic validation SHA-256:
  `abf429fc6a029b8dae3f50c64c9e119b9f0a9f0f1e3e44a6b01b82b709048545`.
- Synthetic complete-manifest SHA-256:
  `bbbc852498b1817a9969d2bcf18d35137d5ddaeadc4c0be4b0299171d6f17b02`.
- Final packet-index SHA-256:
  `79a52807a96aa18cc222a81a1e96c18babd1071a593c0417079e716f41ab3c79`.
- Final draft-manifest SHA-256:
  `63446daec35783be36f1bf75f2edbd8bc07c6e45930c68a3e7ab5aee782e8a65`.
- UI template SHA-256:
  `61dea7af8342f7e1fc8bf2c913694b4d1dc3605493ca4ff8cf48f8b870024f59`.
- Packet builder SHA-256:
  `8a4d5c6150dce72db98e6625501161bd0f834aedcac4a8371403c359e5ef1d4f`.

## Gate decision

- Stage 7.3C engineering is complete after packet/gold self-tests, Ruff, JavaScript syntax,
  focused contract, formatting, Clippy, and full workspace tests pass.
- Synthetic QA proves mechanics and interchange only; it is not human truth and cannot
  authorize ordering-rule or provider changes.
- Stage 7.3D must run a timed two-reviewer blind pilot, record reviewer agreement and
  adjudication workload, then validate a real completed manifest.
- No real private human gold exists yet. Stage 7.4 remains forbidden until the Stage 7.3D
  manifest is READY and all quality thresholds pass.
