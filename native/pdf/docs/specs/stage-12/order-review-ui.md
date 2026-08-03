# Stage 12 Stage 7.3C Blind Brush Workbench

Status: implemented and verified; Stage 7.3D pilot may begin; Stage 7.4 remains forbidden

## Primary task and user flow

Primary task: create one human reading block with one brush stroke, repeat in reading
order, and classify margin furniture without reading every BBox individually.

1. A reviewer opens a locked Reviewer A or Reviewer B workspace.
2. In Main flow mode, pointer-down starts a stroke, pointer movement collects visible
   nodes, and pointer-up commits one block and one undo transaction.
3. Each new main-flow block is appended to block reading order. Consecutive block
   precedence pairs are generated from that order.
4. The reviewer switches role and sweeps page headers, footers, page numbers, or
   other artifacts. These blocks do not enter reading order.
5. The reviewer uses erase, merge, split, block reorder, or undo only when the first
   stroke was inaccurate.
6. The page becomes complete when every node belongs to exactly one block. The
   reviewer exports a reviewer-only schema-v2 manifest.
7. An adjudicator imports both reviewer files, compares canonical block summaries,
   copies or edits a resolution, chooses coded reasons, and exports the final manifest.

Brush strokes are UI transactions only. The persisted manifest stores node IDs,
block IDs, roles, `internal_order=unspecified`, and block precedence pairs. It never
stores a raster mask, pointer trace, PDF text, source path, or parser hint.

## Task model

| Level | Goal |
|---|---|
| Primary | Brush one block at a time in human reading order |
| Secondary | Sweep furniture/artifacts and complete page coverage |
| Low-frequency | Erase, merge, split, reorder blocks, undo, zoom, next incomplete |
| Rare | Import conflict recovery, reset profile, coded adjudication |

## State model

| State | Entry | Must show | Hidden | Primary action | Exit |
|---|---|---|---|---|---|
| `empty` | Page has no committed blocks | Neutral nodes, brush role, short first-action hint | Parser roles/order/confidence/features | Brush first block | Pointer-up commits a block |
| `drafting` | Stroke is active | Stroke path, candidate nodes, cancel affordance | Import/export and long help | Continue or release pointer | Commit/cancel |
| `editing` | Page has blocks but incomplete coverage | Block order, selected block, unassigned count, undo | Other reviewer data | Brush next block | Full coverage |
| `page_complete` | Every node belongs to one block and precedence is valid | Completion state, block list, next incomplete | First-use help | Next incomplete | Another page opens |
| `review_complete` | Locked reviewer completed all pages | Reviewer-only export | Other review/adjudication labels | Export | File downloaded |
| `adjudication_needed` | Both imports valid and canonical labels differ | A/B summaries, copy controls, reason codes | Reviewer editing shortcuts | Resolve page | All disagreements resolved |
| `ready` | Reviews and adjudication are complete | Final export and VALID-ready state | Draft guidance | Export complete manifest | Authority validator accepts |
| `blocked` | Invalid import, incomplete page, or identity mismatch | Exact cause and recovery action | Unrelated controls | Correct issue | Validation succeeds |

A pointer cancel, lost capture, or Escape during `drafting` restores the pre-stroke
state and creates no undo entry.

## Information architecture

| Item | Role | Frequency | First viewport | Show condition | Container | Collapsible |
|---|---|---:|---:|---|---|---:|
| Clean page and neutral node hit areas | action-critical | High | Yes | Always | Main stage | No |
| Brush/erase/pan and active block role | action-critical | High | Yes | Reviewer edit mode | Sticky tool rail | No |
| Block sequence and selected block | action-critical | High | Yes | Blocks exist | Context inspector | No |
| Unassigned node count/page progress | status-feedback | High | Yes | Always | Command bar | No |
| Undo/merge/split/reorder | correction | Medium | Yes | Valid selected block/action | Context inspector | No |
| Zoom/fit | navigation | Medium | Yes | Always | Stage toolbar | No |
| Next incomplete | action-critical | Medium | Yes | Incomplete work exists | Inspector footer | No |
| Import/export | decision-supporting | Low | No | File actions opened or review complete | Disclosure | Yes |
| Keyboard/pointer help | reference | Low | No | Help opened | Disclosure | Yes |
| A/B comparison and reasons | exception-handling | Low | No | Adjudication profile | Inspector | No |
| Reset profile/page | destructive | Rare | No | Danger disclosure opened | Disclosure | Yes |

## Content audit and visibility plan

- `must-see-now`: page, neutral hit areas, active tool/role, current stroke,
  block sequence, selected block, unassigned count, completion, undo.
- `next-step-only`: next incomplete after page completion; reviewer export after
  review completion; final export after adjudication.
- `error-only`: pointer cancellation, duplicate/unknown node, schema/identity
  mismatch, invalid block graph, browser storage failure.
- `on-demand-reference`: shortcut legend, schema explanation, import/export help.
- `keep-off-first-viewport`: raw JSON, packet statistics, reset controls, other
  reviewer labels, parser roles/order/confidence, feature codes.

Deferred blocks:

| Block | hidden_now_because | reveal_trigger | container |
|---|---|---|---|
| Other reviewer labels | Independence would be compromised | Both reviewer-only manifests imported | Adjudication inspector |
| Reason codes | They are irrelevant on canonical agreement | Current page differs | Inline adjudication fieldset |
| File details | They do not advance brushing | User opens file actions | Details disclosure |
| Reset actions | Destructive and rare | User opens danger actions | Details disclosure |
| Schema/shortcuts | Reference content competes with the page | User opens help | Details disclosure |

## Blind reviewer contract

Reviewer HTML must not contain or reveal Rust semantic role, artifact flag, inferred
order, confidence, rule IDs, feature codes, or role-derived colors/tooltips. All
unassigned nodes use the same neutral outline. Assigned nodes use only the human
block color/number chosen in this workspace. The adjudicator may see Reviewer A/B
human labels but still receives no Rust hints.

The packet builder may use parser roles privately for deterministic page sampling,
but interactive page payloads contain only document/page identity, relative clean
image path, node ID, and display-clipped percent box. Static coordinate overlays use
a neutral outline and remain QA evidence, not reviewer hints.

## Brush and correction mechanics

- `pointerdown` opens a transaction and captures the pointer.
- `pointermove` adds every node whose display box, inflated by a small screen-space
  tolerance, intersects the stroke segment. Re-entering a node is idempotent.
- `pointerup` commits one block or one erase/split operation and pushes exactly one
  undo snapshot. An empty stroke is a no-op.
- Erase removes painted nodes from their blocks; empty blocks are removed and
  precedence is regenerated.
- Split mode is available only with a selected multi-node block. The next stroke
  moves the painted subset into a new adjacent block of the same role.
- Merge combines the selected block with the previous compatible-role block.
- Main-flow blocks can move up/down; adjacent precedence pairs are regenerated.
- Stable page-local IDs use monotonically increasing `b0001` form. Undo restores
  prior IDs and state; deleted IDs are not reused during the same page session.
- Pan never edits. Zoom is clamped and does not alter stored percent boxes.
- Touch and pen use the same pointer transaction contract; mouse-only events are forbidden.

## Persistence, isolation, and adjudication

- Reviewer A/B pages are separate locked HTML workspaces and storage keys.
- Reviewer-only exports blank the other reviewer and adjudication labels.
- Import validates schema version, packet/document/page identity, node coverage,
  block membership, roles, and acyclic precedence before merge.
- Canonical comparison ignores reviewer-local block IDs and compares role plus sorted
  membership and canonical precedence.
- Equal pages can auto-adjudicate. Differing pages require one or more coded reasons.
- `status=complete` is emitted only after both reviews and every adjudicated page
  pass schema-v2 completeness.

## Design and accessibility

- The page owns the largest surface; the inspector is contextual, not a dashboard.
- CSS tokens own color, spacing, radius, shadow, typography, and motion.
- Active tool/role is communicated by text, icon, shape, and `aria-pressed`, not
  color alone. Controls have visible focus and at least 40 px touch targets.
- `B` selects brush, `E` erase, `P` pan, `1..5` selects role, `Ctrl+Z`
  undo, `Escape` cancels a stroke, and arrow keys change page only when focus is
  outside an input.
- Desktop uses page plus inspector; tablet/mobile stack the inspector below the page
  without horizontal viewport overflow. Zoomed page content scrolls inside the stage.
- Empty, drafting, selected, complete, disabled, hover, active, focus, error, and
  reduced-motion states are explicit.

## Verification

- DOM self-test must prove pointer-down/move/up commits one block and one undo item;
  erase, split, merge, reorder, cancel, and zoom bounds behave deterministically.
- Synthetic browser QA must complete all pages in both reviewer profiles, introduce
  one coded disagreement, export schema v2, and pass `stage12_order_gold.py`.
- Inspect desktop, tablet, and mobile screenshots plus a high-density page.
- Audit generated reviewer HTML for forbidden Rust hint keys and private text/path/URL.
- Run packet/gold self-tests, UI syntax, frontend deterministic audit, Ruff,
  Stage 12 contract, formatting, Clippy with warnings denied, workspace tests, and doctests.
