---
name: lifeos-knowledge-sync
description: Synchronize reviewed Life OS project documents into the engineering knowledge index with receipts, rollback safety, and no product-data mixing.
---

# Life OS knowledge sync

## Applicability

Use after QA and final review when factual project documents must be indexed, an
index attempt failed, or source-baseline receipts need reconciliation.

## Steps

1. Verify task, candidate, source HEAD, QA receipt, and final review approval.
2. Publish only the fixed allowed documents through the existing publisher.
3. Capture before and after hashes and preserve an independent private backup.
4. Synchronize the reviewed documents through the existing knowledge store.
5. Retry a failed stage with the same event identity and detect concurrent edits.

## Safety

- Do not use the product database, personal records, raw transcripts, credentials,
  or an unresolved source baseline as engineering index input.
- Do not overwrite user prose or treat a failed index as delivery-ready.
- Do not claim SQLite plus filesystem atomicity.

## Output

Report document receipts, old and new hashes, index event ID, retry state, backup
location, remaining failures, and whether delivery is still closed.

## Acceptance

Acceptance requires actual document and index receipts, successful recovery from
injected failures, preserved history, and no hidden source drift.
