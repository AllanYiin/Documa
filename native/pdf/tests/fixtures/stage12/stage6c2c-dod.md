# Stage 12 Stage 6C2-C Definition of Done

Status: Complete; Stage 6C2-D may begin

## Scope completed

- `TaggedStructureResult` is converted once into bounded page-indexed association
  and table buckets, with explicit unindexed buckets and exact membership checks.
- Navigation is extracted once into page link buckets plus document-level named
  destinations and outlines before page production.
- Reading order, tagged text/Figure associations, tagged/vector/text tables,
  image placements, caption flow, and page links are applied to one `PageLayout`
  at a time. Positioned glyphs, marked content, operations, MCID maps, and vector
  segments are released after the page-local rules consume them.
- Warning payloads remain in the historical stage order. Compact warning-key
  states preserve cross-stage de-duplication without retaining full page inputs.
- A bounded compatibility rollback preserves the former document-wide vector
  failure contract by restoring tagged-table snapshots, clearing image placements,
  and recomputing page-local optional semantics only on that recovery path.

## Exactness and performance evidence

- Reading/tagged/table/image/figure/navigation focused suites pass 55/55.
- The frozen 7-document/1,113-page corpus matches every Stage 6C2-B canonical
  SHA-256, first-run serialized byte count, and aggregate count.
- Final combined C/D privacy-safe report:
  `target/stage12-stage6c2cd-page-local-final/report.json`.
- Report SHA-256:
  `149f92aaf43a4806a36b76e373fc6dcb9070c08c1f43f289195bcdcbc9f9bcfb`.
- Core throughput: 182.426213 pages/s.
- Core peak RSS: 434,147,328 bytes.
- One serialized run remains exactly 427,050,863 bytes; schema, privacy, and
  determinism audits pass 7/7.

## Gate decision

Stage 6C2-C is complete and Stage 6C2-D may begin. This substage proves page-local
semantic application but does not claim that the public event iterator is lazy:
the compatibility build still retains the complete `Vec<PageLayout>`. Default-provider cutover remains forbidden.
