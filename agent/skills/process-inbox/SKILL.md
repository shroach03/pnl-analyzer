---
name: process-inbox
description: Run the P&L Analyzer intake sweep: identify, dedup, file, quarantine, and certify every document in the Google Drive Inbox/ folder. Use whenever the user says "process the inbox", "run the sweep", "check the inbox", asks whether new accountant documents arrived, or starts a monthly review (the sweep is always Phase 1 of a monthly run).
---

# Process the Inbox (agent intake sweep, Phase 1 of the monthly cycle)

You are the intake half of the monthly financial review pipeline. The Apps Script
(`apps-script/Code.gs`) pulls accountant email attachments into `Inbox/` on Google Drive;
your job is to disposition every file there. Nothing may be left undispositioned.

This is deliberately the cheap phase (target ~10k tokens). Identify and classify from the
**first 2–3 pages** of each PDF, not page 1 alone: accountant packets often put a cover
letter ahead of the P&L, some bundle the bank reconciliation deeper in the packet, and a
store code may only appear in the statement header. Stop once 2 identity fields are
confirmed; don't read past page 3 hunting for a third.

Never extract P&L lines, GL transactions, or run ratio/anomaly analysis here. That happens
per store in Phase 2 (see the `analyze-month` skill), so doing it during intake would mean
paying for it twice.

## Authoritative procedure

1. Follow **`procedures/sweep_procedure.md`** exactly. It contains the step-by-step
   procedure, the disposition table, the intake-log format, and the certification format.
2. Load **`stores_registry.json`** (identity matching rules) and **`intake_log.json`**
   (SHA-256 dedup index + append-only disposition history) before touching any file.

Do not improvise around the procedure. In particular:

- **Identity comes from document content, never from filenames or email subjects.**
  Two-registry-field match or Quarantine. Never guess.
- **Filing is COPY** (the Drive connector cannot move or delete). Removal happens only via
  the `_processed_*.json` certification the script's `cleanupInbox()` executes.
- **One certification per run.** Initialize a single trash list at the start and append
  every cleanup candidate to it (Inbox dispositions, superseded files). Write exactly one
  `Inbox/_processed_{stamp}.json` at the end. Never create a second certification
  mid-run for something noticed along the way; append to the one list instead.
- **Never list `_manifest.csv` for trash.** Only certify files whose disposition is
  complete and verified.
- **Every disposition is appended to `intake_log.json`.** Append-only; never rewrite history.
- End by reporting: cleanup errors (if any), the completeness matrix, a dispositions
  table, the filed-only count line, and any CHASE flags.

## Preconditions

- Google Drive connector enabled. If its tools are deferred, load them; if the connector
  is off, stop and ask the user to enable it.
- If `procedures/sweep_procedure.md` cannot be found, stop and say so. Do not
  reconstruct the procedure from memory.

## After the sweep

Tell the user the certification is written and that the Apps Script's next run
(≤ 30 min, or a manual `cleanupInbox()`) will empty the Inbox. If the user then asks to
"run the monthly review", proceed to Phase 2 of `analyze-month`.
