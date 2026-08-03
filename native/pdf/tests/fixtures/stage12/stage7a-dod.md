# Stage 12 Stage 7.1 Definition of Done

Status: Page-level localization complete; Stage 7.2 may begin

## Scope and boundary

- The profiler covers 7 documents / 1,113 pages with separate provider worker
  processes and exact page-number alignment.
- Complete character and bigram counters exist only in an operating-system
  temporary directory. The directory is removed on success or failure before a
  report can be accepted.
- The final report contains only lengths, precision/recall/F1, Unicode
  category/script deltas, structural counts, warning codes, and reason
  candidates. It contains no extracted text, character keys, source paths,
  URLs, complete counters, or private IR.
- PyMuPDF remains an offline comparison oracle, not runtime truth or human gold.

## Formal corpus evidence

- Report:
  `target/stage12-stage7a-page-quality/report.json`
- Report SHA-256:
  `b365b13e643f2fd32c9e386e219c07e11bca23f0bf631958fa3e0a527f266f4e`
- Stage 6D reference report SHA-256:
  `245966517805ae6d4689355307c7bd12e1f8675b41b87476bc600759a10ac44d`
- All seven per-case character/bigram scores and both aggregate scores reproduce
  Stage 6D within `1e-12`.
- Aggregate non-whitespace character F1 is `0.9608131914224296`
  (precision `0.9993134542250104`, recall `0.9251694532765342`).
- Aggregate character-bigram F1 is `0.9512812708818802`
  (precision `0.9894062147080588`, recall `0.9159854604200323`).
- 438 pages are below character F1 0.995; 593 pages are below bigram F1 0.99.
- Rust is shorter on 444 pages and longer on 32 pages. The worst character
  pages concentrate in the 580-page document, with a smaller severe cluster in
  AI Index.
- Net Unicode-category deltas are led by `Sm -91,371`, `Ll -42,049`,
  `Nd -6,416`, and `Lu -4,586`. This is localization evidence, not proof that
  every PyMuPDF symbol is desirable user-visible text.

## Interpretation and Stage 7.2 handoff

- Precision near 1.0 with materially lower recall is consistent with missing
  extraction or mapping being the first investigation target. Reading-order
  changes alone cannot close the character-multiset gap.
- `page_furniture_ambiguous` occurs on 961 pages and is too broad to be treated
  as a root cause. More selective signals include `reading_order_ambiguous`,
  tagged MCID warnings, fallback encoding, and Unicode mapping warnings.
- Stage 7.2 must inspect the worst privacy-safe page clusters against source
  operators/font mappings and small reviewed samples. It must separate useful
  text from decorative or symbolic glyphs before changing extraction behavior.
- No parser behavior, public schema, Documa mapping, or provider default changes
  in this stage. Default-provider cutover remains forbidden.

## Validation

- `python -B tools/stage12_page_quality_diff.py --self-test`: PASS.
- Formal one-run localization: PASS, 7/7 cases and 1,113/1,113 pages aligned.
- Privacy denylist and temporary cleanup assertions: PASS.
- Stage 12 focused contract, formatting, Clippy, and full workspace tests are
  required by the stage gate and are recorded in `DEVNOTE.md`.