# Stage 12 Stage 2 Tagged Structure Contract

Specification version: 1
Status: Complete (2026-07-29)

## Goal

Build a bounded, deterministic association between marked page content and the
PDF logical structure tree. Populate Layout IR tagged order and semantic roles
without changing legacy text extraction output or treating MCID as an order by
itself.

## Users and success scenarios

The direct users are `pdf-core` maintainers and the later Documa Rust adapter.
They need to:

1. distinguish source order from author-provided tagged order;
2. trace a tagged node back to page object, MCID, source ordinals, and rule;
3. retain and classify artifacts, including page furniture and page numbers;
4. expose accessible descriptions without duplicating or replacing visible text;
5. fall back to source order when optional tagged metadata is absent or invalid.

## Non-goals

- Stage 2 does not infer human reading order for untagged or damaged documents.
- Stage 2 does not classify repeated headers, footers, or page numbers by geometry.
- Stage 2 does not reconstruct table topology or image placement geometry.
- Stage 2 does not consume `/Alt` as visible replacement text.
- Stage 2 does not expose raw PDF dictionaries through bindings.
- Stage 2 does not change the Documa default provider.

## Input model

### Marked page content

`BMC` and `BDC` record the tag name. `BDC` additionally resolves direct, named,
or indirect property dictionaries and recognizes:

- `/MCID`: non-negative marked-content identifier;
- `/ActualText`: existing authoritative replacement text;
- `/Alt`: accessible description stored as metadata only;
- `/Artifact`: the tag itself marks the entire nested scope as artifact.

Nested scopes inherit artifact state. The nearest available MCID, tag, and Alt
value are attached to each internally collected glyph. This metadata is private
to `pdf-core` and must not change the legacy `ExtractedTextV2` JSON shape.

### Structure tree

The catalog may contain `/StructTreeRoot`. Stage 2 traverses root and structure
element `/K` entries in declared order. Supported content references are:

- integer MCID with an inherited structure-element `/Pg`;
- marked-content reference dictionaries with `/MCID` and optional `/Pg`;
- nested direct or indirect structure-element dictionaries;
- arrays containing the supported forms above.

`/RoleMap` chains custom roles to standard roles with cycle and depth checks.
`/ParentTree` number-tree `/Nums` and `/Kids`, page `/StructParents`, and content
MCIDs are validated as a reverse-association consistency check. Object-reference
kids and stream-associated MCRs are preserved as warnings until Stage 5 rather
than silently interpreted as text order.

## Output model

Layout IR schema version remains 1 with additive Stage 2 fields:

- node `tag`: original structure or marked-content tag when known;
- node `alt_text`: accessible description when valid;
- node `actual_text`: structure-level ActualText metadata when valid;
- node `artifact`: explicit boolean;
- semantic roles expanded from unclassified to standard structural categories;
- `tagged_order`: node IDs in structure-tree order for that page;
- document capability `tagged_order`: true only when at least one valid tagged
  association is produced;
- document capability `semantic_roles`: true only when at least one node role is
  supported by a direct standard role or a valid RoleMap chain.

`source_order` remains complete and includes artifacts and untagged content.
`tagged_order` contains only structure-tree-associated content. Empty tagged
order never aliases source order. Stage 3 owns inferred order and main flow.

## Text precedence

1. Valid marked-content `/ActualText` replaces enclosed visible text exactly once.
2. Font ToUnicode and documented fallback continue to decode other visible text.
3. Structure-element `/ActualText` is metadata and does not duplicate content.
4. `/Alt` is metadata and never replaces visible text.
5. Artifact text is preserved in source order and marked `artifact = true`.

## Bounds and security

All input-derived work is bounded by explicit `ParseLimits` values for structure
elements, structure kids, ParentTree entries, RoleMap entries, and existing
object depth (`max_object_depth`). Every integer conversion, page lookup, MCID, collection growth,
and recursion step is checked. Core remains `#![forbid(unsafe_code)]` and adds no
PDF-aware dependency.

A structure limit breach is fatal with stable `limit_exceeded`. Other malformed
optional tagged metadata must preserve extracted source text, disable only the
unsupported tagged association, and emit an aggregated stable warning.

## Stable warnings

- `tagged_structure_invalid`: malformed optional root, element, kid, page, or role;
- `tagged_structure_cycle`: cyclic structure or number-tree reference;
- `tagged_mcid_missing`: structure tree references no collected content item;
- `tagged_mcid_ambiguous`: duplicate content or structure association;
- `parent_tree_mismatch`: ParentTree does not confirm the page/MCID association;
- `marked_content_invalid`: malformed MCID, Alt, or marked-content properties;
- `tagged_object_reference_unsupported`: OBJR or stream-associated MCR deferred.

Warnings aggregate by page and relevant role or MCID key. Human-readable messages
are diagnostic; consumers branch only on stable codes.

## Compatibility and bindings

Rust, CLI, Python, and WASM continue to expose the same Layout IR root and schema
version. Bindings add no PDF syntax logic. Legacy `extract_text`,
`extract_text_v2`, CLI extract JSON, Python extract APIs, and WASM text APIs keep
their existing serialized fields and semantics.

## Observability

The Stage 2 private report may store only counts, hashes, capability totals,
warning-code counts, durations, and audit booleans. It must set
`contains_extracted_content` to false and never persist Layout IR, Alt,
ActualText, tags from private content, or visible text.

## Test strategy

Synthetic tests cover:

- direct and indirect StructTreeRoot and StructElem nodes;
- integer, dictionary, array, and nested `/K` forms;
- page inheritance through `/Pg` and page lookup by object id;
- RoleMap mapping and cycles;
- ParentTree number-tree Nums/Kids and `/StructParents` consistency;
- BMC/BDC tags, nested Artifact inheritance, Alt, and ActualText precedence;
- duplicate, missing, negative, wrong-page, and out-of-range MCIDs;
- direct/reference cycles, depth, entry, and kid exact boundaries;
- empty, untagged, and malformed optional structure without visible-text loss;
- deterministic Rust serialization and CLI/Python/WASM schema parity.

The frozen corpus report measures tagged pages, associated MCIDs, artifacts,
roles, missing/ambiguous links, deterministic hashes, elapsed time, and privacy.

## Completion result

The frozen 7-document/1,113-page corpus passed deterministic schema and privacy
audits. Stage 2 processes 218.501999 pages/s, 30.067210x the frozen Stage 0
Documa adapter baseline. Six untagged documents retain byte-identical Stage 1B
Layout IR; only the tagged document carries the additive metadata. The detailed
non-private evidence is in `tests/fixtures/stage12/stage2-dod.md`.
## Definition of Done

- [x] Marked-content tag, Alt, ActualText, MCID, and inherited Artifact state are captured internally.
- [x] Legacy extraction DTO and JSON shapes are unchanged by the internal metadata.
- [x] StructTreeRoot, StructElem K, Pg, RoleMap, and ParentTree are traversed within explicit limits.
- [x] Layout nodes expose deterministic tagged metadata, roles, and provenance.
- [x] Tagged order is structure-derived, non-aliasing, and capability-gated.
- [x] Artifact content is preserved and classified, never silently deleted.
- [x] Alt does not replace text; ActualText does not duplicate text.
- [x] Stable warnings cover malformed, cyclic, missing, ambiguous, and deferred cases.
- [x] Synthetic boundary and malformed-input tests pass.
- [x] Rust, CLI, Python, and WASM expose the additive schema consistently.
- [x] A deterministic privacy-safe frozen-corpus Stage 2 report is recorded.
- [x] Focused tests, formatting, Clippy with denied warnings, and workspace tests pass.

Default-provider cutover remains forbidden after Stage 2. Stage 3 must still
supply human reading order, paragraph grouping, and page-furniture classification.