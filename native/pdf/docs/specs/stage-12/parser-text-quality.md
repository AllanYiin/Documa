# Stage 12 parser text quality layers

Status: Stage 7.2 raw parser oracle calibration complete

## Decision

Text completeness, adapter semantics, and human reading quality are separate gates.
A score from one layer must not be presented as a score for another layer.

1. Raw parser extraction compares Rust Layout source-order node text with
   PyMuPDF raw text blocks before table, image, or domain rewriting.
2. Documa adapter integration compares the final `DocumentIR` produced by each
   adapter. It measures integration behavior, including table reconstruction.
3. Human semantic gold measures useful text, reading order, artifacts, tables,
   figures, and captions against reviewed labels.

The Stage 6D/7.1 complete-adapter character score includes PyMuPDF
`find_tables()` output. Table rows can replace overlapping text blocks and may
repeat or synthesize delimiters. That score remains useful integration evidence
but must not be used as parser text truth.

## Raw parser extraction contract

- Rust source is each page's semantic nodes in explicit `source_order`.
- PyMuPDF source is `page.get_text("dict", sort=False)` text blocks before
  Documa `_parse_tables()` runs.
- Whitespace is removed only for the character-multiset metric. Character
  bigrams remain a source-order proxy, not human reading-order gold.
- Complete counters are temporary. Reports contain only scores, lengths,
  Unicode category/script deltas, warning codes, and structural counts.
- Page number, page count, file SHA-256, and frozen corpus membership must align
  before scoring.

## Geometry and downstream semantics

This calibration does not change the coordinate contract: CropBox top-left,
x right, y down, points after UserUnit, page Rotate metadata only, and exactly
one PDF-to-layout projection after content/Form matrices.

Rust continues to own deterministic PDF syntax, text decoding, geometry, and
bounded structural evidence. Documa continues to own cross-page, domain, and
LLM semantics. Human reading order, table correctness, and image/caption quality
require separate reviewed gold and cannot be inferred from PyMuPDF equality.