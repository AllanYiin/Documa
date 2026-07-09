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
  "reading_order": ["<first-block text prefix>", "<second-block text prefix>"],
  "tables": [
    {
      "table_index": 0,
      "html": "<table><tr><td>...</td></tr></table>"
    }
  ]
}
```

- `reading_order`: expected block sequence as text prefixes (matched against
  actual block text); scored with normalized edit distance (NED).
- `tables[].html`: expected table structure as an HTML tree; scored with
  TEDS / TEDS-S.

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
