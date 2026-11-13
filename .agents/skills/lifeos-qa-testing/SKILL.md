---
name: lifeos-qa-testing
description: Run proportional Life OS QA, preserve actual command and browser evidence, and distinguish unit, file, browser, and device verification limits.
---

# Life OS QA testing

## Applicability

Use when validating a candidate, broker report, regression suite, UI flow,
import/export path, or release gate.

## Steps

1. Read AGENTS, the approved scope, source digest, and prescribed commands.
2. Run immutable commands unchanged and record process status and exit codes.
3. Use synthetic isolated data for CRUD, JSON restore, and storage checks.
4. Check narrow, short, and desktop layouts, bottom navigation, console, and PWA basics.
5. Validate every broker check individually and convert any error, timeout, or
   unsupported required check to FAIL.

## Safety

- Never read credentials, use a daily browser profile, import real JSON, reuse
  production ports, or mutate the candidate under test.
- Do not let N/A, Builder claims, arbitrary PASS JSON, or screenshots replace
  actual independent receipts.
- Do not claim real authentication, AI tool execution, or physical iPhone tests
  when they are not implemented.

## Output

Report candidate digest, source inventory digest, commands, process records,
checks, failures, timeouts, private artifact locations, and unimplemented scope.

## Acceptance

Acceptance requires all required checks to pass, exact command receipts to exit
zero, visible product limitations to remain explicit, and an independent final
review.
