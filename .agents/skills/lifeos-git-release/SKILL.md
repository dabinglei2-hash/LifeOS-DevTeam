---
name: lifeos-git-release
description: Review and prepare Life OS Git release changes without committing, pushing, or deploying unless the user separately authorizes those actions.
---

# Life OS Git release

## Applicability

Use when changes are ready for repository review, a release candidate must be
identified, or staged files and commit readiness need inspection.

## Steps

1. Read AGENTS and verify branch, HEAD, status, allowed paths, and review scope.
2. Inspect the actual diff and test evidence; do not infer success from reports.
3. Confirm unrelated user changes remain untouched and protected files are excluded.
4. When explicitly authorized, prepare a narrow commit and release notes.
5. Recheck the post-action state and report the resulting revision.

## Safety

- Never run reset --hard, checkout --, clean, push, merge, tag publication, or
  deployment without separate user authorization.
- Do not stage unrelated files or hide source-baseline drift.
- Do not treat technical review as release or deployment approval.

## Output

Report HEAD, branch, reviewed paths, excluded changes, tests, release candidate,
and any action still requiring explicit authorization.

## Acceptance

Acceptance requires an inspected diff, reproducible checks, preserved unrelated
work, and authorization matching the requested Git mutation.
