# Release recovery Builder ACK (2026-11-15)

## Scope and attribution

- Task: `e383fe89-3300-4c4e-9196-f050093f89df`
- Source HEAD: `7b7b16fbaf94b545b7e9df3b8ea368d1a7e9bf42`
- Prepared inventory digest: `3a29eee94af1b300fb066bffe3cb2e65d4c55f7876372f46855770dbee3b8c22`
- Source snapshot SHA-256: `597637fbf48b01f930b0c461c0a77e12ddc4aaa901fca25b5e54eafb62e4b39f`

The release-recovery implementation, its recovery records, and its synthetic
regression tests are main-authored by GPT/Codex. This Builder run is ACK-only:
it created only this file and did not author or modify implementation code,
existing documentation, or tests.

Builder did not commit, push, create a PR, call GitHub, deploy, install
dependencies, run migrations, or access credentials, private databases,
personal records, or raw backup contents.

## Verified baseline

The independent worktree is detached at the declared source HEAD. Its complete
161-file inventory, excluding `.git`, reproduced the declared source snapshot
SHA-256. The seven prepared paths reproduced the supplied canonical inventory
digest, and every path matched its declared SHA-256:

| Path | SHA-256 |
| --- | --- |
| `docs/ARCHITECTURE.md` | `39f4deff5d38bf0d2be0340661ae2416a63b5795d5fc457f925a7af4f78fe1f3` |
| `docs/DEVELOPMENT_PROGRESS.md` | `e76375993d5acf7bc9ad1ad6d29685cc10103dfa3dbd6f19297352abac914eed` |
| `docs/KNOWN_ISSUES.md` | `06a1447d5bafa317b08701fcb81c2795574feed2d2e4ea12026592b526a6aaf6` |
| `docs/RELEASE_RECOVERY_20261115.md` | `dbfe8b9c9f8a3828702a47d59807d58fb46c8550e11aa383a7ffc9658e132e5a` |
| `docs/STATUS.md` | `d003a8e1b056e28ea121fd585d69c5e895c3d4d2c14f4381cff956861dc65334` |
| `lads_automation/github_release.py` | `6238dd0e286ae1de17e7adfec1d55343292cbb8d833e993bf96062cb962d687b` |
| `tests/automation/test_release_merge_race.py` | `bf23dc22383155765b6ad0c0fe43ef103c9820ef79b65feefa197ecec6809d72` |

The controller's build-1 manifest is present, task-bound, and records this ACK
path as originally absent. No additional backup, cache, log, bytecode, or
helper file was created.

## Engineering acknowledgement

Builder read the complete seven-file delta. Polling now re-fetches the same
pinned PR after the REST OPEN/GraphQL MERGED conflict and returns only after
the durable native evidence and bound controller status are revalidated.
Already-merged resume occurs before the pre-merge base-drift check, but after
authorization, local-gate, repository, and audit checks. It then revalidates
native evidence, current protection, required CI, exact merge ancestry, and
the existing knowledge-receipt path.

Open or unproven releases remain blocked, invalid pinned PR numbers are
rejected, and the resume tests assert there is no duplicate capture, push, or
commit. No genuine blocking defect was found in the inspected scope.

Residual risk: `_poll_merge` catches `ReleaseConflict` broadly. Reconciliation
still requires exact durable native evidence, and the tests directly preserve
an OPEN conflict, but there is no separate synthetic test for a merged
non-race conflict rejected by that evidence check.

## Evidence boundary

Main's targeted result is recorded as nine new and 67 existing tests passing;
Builder did not rerun that suite, and it is not independent QA evidence.
Builder also did not run the four prescribed sealed-verifier commands, which
remain exclusively for QA. The supplied Scanner and PLAN Reviewer reports
were read, but Builder makes no claim about their process routing or identity.

The original PR #4 race, ordinary-resume failure, failed attempts, and their
counters remain preserved. This ACK neither rewrites that history nor claims a
new GitHub merge, final review, release approval, automatic-release enablement,
deployment, or production readiness.
