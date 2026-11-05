## Summary

Normalizes `dot_number` in an ingest pipeline for every dataset carriers enriches from. FMCSA zero-pads `dot_number` to eight characters in `auth_history.csv`, so the padded `'00023680'` never matched `'23680'` in `carriers` until the pipeline converted both to one canonical string.

## Measured

- `carriers` enrichment from `auth_history`: 0 -> 546,042 documents
- `carriers` enrichment from `boc3_agents`: 0 -> 565,299 documents
- rebuilt index `carriers-2026.08.13-000001`: 2,178,352 documents

An affected record looks like <CARRIER-A>: padded on one side of the join, unpadded on the other.
