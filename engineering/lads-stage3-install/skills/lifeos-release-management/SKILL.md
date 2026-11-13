---
name: lifeos-release-management
description: Coordinate Life OS release readiness from reviewed evidence and explicit authorization while keeping install, commit, publish, and deploy actions separate.
---

# Life OS release management

## Applicability

Use when a reviewed candidate, protected install plan, release checklist, or
deployment decision is being handed to the main controller.

## Steps

1. Verify the candidate and install-map hashes, source HEAD, source snapshot,
   QA result, and final review status.
2. Confirm every required check has actual evidence and every limitation remains visible.
3. Back up protected targets and validate current hashes before installation.
4. Obtain separate authorization for each commit, push, publish, deploy, or migration.
5. Record post-action receipts and retain the previous revision for recovery.

## Safety

- Never install directly from an unreviewed Builder report.
- Never combine technical approval with deployment authorization.
- Never bypass native hook trust, sandbox restrictions, or source-drift checks.

## Output

Report release candidate, exact install map, backup plan, approvals, executed
actions, post-action hashes, rollback location, and unresolved PENDING items.

## Acceptance

Acceptance requires exact reviewed bytes, independent final approval, explicit
user authorization for mutations, successful post-action verification, and a
documented recovery point.
