# fixtures/pdf/gold/

Gold (expected) partial IR annotations for quality benchmarking
(`documa benchmark --mode quality`).

Layout: one directory per manifest case id, each containing
`expected.partial.json`:

```text
gold/
  <case_id>/
    expected.partial.json
```

`expected.partial.json` annotates only the aspects the case cares about
(fields are optional; omit what is not annotated):

```json
{
  "case_id": "<manifest case id>",
  "annotator": "<name>",
  "annotated_at": "<ISO 8601 date>",
  "threshold": 0.85,
  "reading_order": ["<first-block text prefix>", "<second-block text prefix>"],
  "tables": [
    {"table_index": 0, "html": "<table><tr><td colspan=\"2\">...</td></tr></table>"}
  ],
  "relations": [
    {"type": "toc_item_to_heading", "from_text": "<prefix>", "to_text": "<prefix>"},
    {"type": "caption_to_image", "from_text": "<prefix>", "to_image_on_page": 1}
  ],
  "excluded_texts": ["<prefix that must be classified page_header/page_footer>"],
  "ocr_expected_texts": ["<string that must appear after OCR>"]
}
```

- `reading_order`: expected block sequence as text prefixes (matched against
  actual block text); scored with normalized edit distance (NED).
- `tables[].html`: expected table structure as an HTML tree; colspan/rowspan
  expand to the extractor grid convention (content in the left-most covered
  cell, other covered cells empty). Scored with TEDS / TEDS-S.
- `relations`: sampled expected links; endpoints resolve by text prefix
  (images via `to_image_on_page`). Scored with anchored precision/recall/F1 —
  unannotated links are never counted as spurious.
- `excluded_texts`: texts that must be classified as page furniture
  (page_header / page_footer).
- `ocr_expected_texts`: strings that must be recovered by the OCR-enabled
  pipeline (whitespace-insensitive match). Cases with this field are skipped
  when the documa[ocr] extra is not installed.
- `threshold`: per-case override of the global pass threshold; lowering it
  requires a dated note explaining why.

Rules:

- A case passes when every score (`teds_s`, `reading_order.score`) reaches the
  threshold (`--quality-threshold`, default 0.85 — provisional until more gold
  cases calibrate it).
- Reading-order anchors must be block-start aligned: each prefix must match the
  beginning of an actual block's text, in true human reading order. Anchors in
  the middle of a merged paragraph can never match.
- A gold directory whose name matches no manifest case id is reported as an
  error, never silently skipped.
- Cases without a gold directory stay in readiness mode (existence checks only).
