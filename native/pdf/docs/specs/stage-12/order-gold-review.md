# Stage 12 Stage 7.3B Block Gold Review

Status: block gold schema v2, validator, and scorer complete; blind brush workbench pending

## Review unit

One review unit is one PDF page plus its stable Rust semantic-node IDs. A human
reviewer groups visible nodes into reading blocks, assigns one role to each block,
and records precedence only between main-flow blocks. Reviewers do not order every
BBox and do not have to inspect page furniture internally.

The persisted manifest stores IDs and labels only. Brush paths, raster masks, PDF
text, page images, source filenames, paths, URLs, and free-form notes are excluded.

## Schema v2

Gold manifests use top-level `schema_version=2`. Candidate files use the same version.

Each label set contains:

- `blocks`: non-empty blocks with a page-local `block_id`, one or more
  `member_node_ids`, one `role`, and `internal_order=unspecified`;
- `block_precedence_pairs`: directed pairs between main-flow block IDs.

Allowed block roles are `main_flow`, `artifact`, `page_header`,
`page_footer`, and `page_number`. Every page node must belong to exactly one
block in completed reviewer and adjudicated labels. Artifact-role blocks never
participate in precedence.

A minimal completed label set is:

```json
{
  "blocks": [
    {
      "block_id": "b0",
      "member_node_ids": ["n0", "n1"],
      "role": "main_flow",
      "internal_order": "unspecified"
    },
    {
      "block_id": "b1",
      "member_node_ids": ["n2"],
      "role": "page_number",
      "internal_order": "unspecified"
    }
  ],
  "block_precedence_pairs": []
}
```

The validator rejects schema v1 with the explicit message that v1 click-per-node
manifests are superseded. No migration is needed because no real private human gold
was completed under v1.

## Validation invariants

- Page, document, reviewer, node, and block identities are unique in their scope.
- A block is non-empty and references only declared page node IDs.
- A node cannot appear in two blocks; completed labels assign every node exactly once.
- `internal_order` is always `unspecified`; block membership is not a hidden total order.
- Precedence pairs are unique, non-self-referential, main-flow-only, acyclic, and
  cover every main-flow block when a page has more than one.
- Unknown nodes/blocks, artifact precedence, duplicate membership, empty blocks,
  incomplete coverage, cycles, and privacy-forbidden keys are hard errors.
- `status=review_required` may contain partial/empty label sets and reports
  `BLOCKED: human_order_review_incomplete`.
- An empty private template must be `status=unconfigured` and reports
  `BLOCKED: human_order_gold_unconfigured`.

## Candidate scoring

A candidate remains parser-oriented: full `inferred_order`,
`main_flow_node_ids`, and node-level `artifact_roles`. Candidate schema version
is 2, but it does not copy human block membership.

For each gold block precedence pair A -> B, the scorer evaluates every cross-node
pair (a in A, b in B). Its pair score is the fraction where both nodes are present
and the candidate places a before b. Global `block_pair_concordance.macro_accuracy`
is the equal-weight average over gold block pairs. Therefore a large block does not
outvote a small block merely because it contains more nodes. Cross-node totals are
reported as a diagnostic only.

Main-flow membership uses node-level precision/recall/F1. Artifact-role accuracy
compares the expected role of every artifact node. A score passes only when block
pair macro concordance is at least 0.95; this does not authorize provider cutover.

## Two-reviewer and adjudication rule

At least two pseudonymous reviewer IDs are required per page. Reviewers work
independently. Reviewer agreement is block-ID-independent: it canonicalizes each block as its role plus sorted member
node IDs, so equivalent labels remain equal even when reviewer-local block IDs
differ. It compares block partition, block precedence Jaccard, and artifact roles.

If reviewer labels differ, adjudication records at least one non-content reason code:

- `column_order`
- `sidebar_policy`
- `artifact_policy`
- `caption_anchor`
- `rotation_policy`
- `tag_conflict`
- `block_membership`
- `other_reviewed`

Free-form notes remain forbidden. Detailed discussion may stay in the private review
system, outside manifests and repository reports.

## Stage boundaries

Stage 7.3A supplied the accepted BBox geometry. Stage 7.3B owns only the schema,
validator, fixtures, scorer, and review contract; it does not modify Rust reading
order. The old v6 click-per-node packet is historical engineering evidence and must
not be used for real review.

Stage 7.3C will generate a fresh schema-v2 packet and blind brush workbench. Brush
strokes are transient input transactions; persisted truth is block membership, role,
and precedence. Reviewer mode must not reveal Rust role, inferred order, confidence,
or feature hints.

Stage 7.4 cannot begin from a `BLOCKED` manifest. It remains forbidden until two
independent real human reviews and coded adjudication are complete.

## Commands

Validate the public schema and scorer:

```powershell
python -B tools\stage12_order_gold.py --self-test
python -B tools\stage12_order_gold.py --manifest tests\fixtures\stage12\quality\order\public-gold.json --candidate tests\fixtures\stage12\quality\order\public-candidate-perfect.json
```

Validate a future completed private manifest:

```powershell
python -B tools\stage12_order_gold.py --manifest <private-manifest.json> --validate-only --output target\stage12-stage7c-block-gold\private-validation.json
```

Private manifests, candidate orders, page images, and review workspaces must not be
committed. Only privacy-safe aggregate score reports may be retained.
