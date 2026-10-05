# Agent Intake Sweep: Executable Procedure ("process the inbox")

The operational procedure for intake. Every step uses only operations proven to work with the Google Drive connector. Run it at the start of every monthly review, or on demand whenever a resend lands.

This is **Phase 1** of the two-phase monthly cycle (see [`../skills/analyze-month/SKILL.md`](../skills/analyze-month/SKILL.md)). Run it directly in the main session, sequentially, never fanned out: dedup and the completeness matrix need to see every prior disposition in order, and it is already cheap (~10k-token target). Identify and classify from the **first 2–3 pages** (not page 1 alone; see step 2.2). Never extract P&L lines or GL transactions, and never run ratio or anomaly analysis here. That is Phase 2, per active store, in its own isolated subagent context.

## Division of labor

- **Agent (Claude):** reads PDFs in place, identifies and classifies from content, copies files to their destinations (creating folders as needed), maintains all state, and certifies completed files for cleanup.
- **Apps Script ([`Code.gs`](../../apps-script/Code.gs)):** pulls mail into `Inbox/` and trashes Inbox files the agent has certified as done (`cleanupInbox()` executes a verbatim list and decides nothing).
- **Connector limits that shape the design:** no delete, no move, no rename-in-place, no overwrite. Filing is always a **copy**; removal is always delegated to the script via the `_processed` list.

## State locations (source of truth)

| Thing | Canonical location | Drive copy? |
|---|---|---|
| `stores_registry.json` | Claude project | Yes; refresh after edits |
| `baselines/SXX_baseline.json` | Claude project | No; the project is the sole copy |
| `intake_log.json` | Claude project | No |
| PDFs, `_manifest.csv`, reports | Google Drive | n/a |

Drive folders are located by title under the project root (`Inbox/`, `Stores/`, `Quarantine/`, `Reports/`, `Portfolio/`, all created by the script's `setupDrive()`). If you cache folder IDs to save lookups, verify them by title search whenever a lookup fails.

## Procedure

### 0. Preflight

1. Confirm the Google Drive connector responds (list `Inbox/`). If not, stop and tell the user to enable it.
2. Check `Inbox/` for `_cleanup_error_*.txt` files from a prior `cleanupInbox()` run. If any exist, read them and surface their contents at the **top** of the sweep summary before doing anything else. A cleanup failure means a previous certification didn't execute.
3. Load `stores_registry.json` and `intake_log.json` from the Claude project. If `intake_log.json` doesn't exist, create it as `{"entries": []}` (first sweep only).
4. Initialize **one** empty run-scoped trash list. Every step below that identifies a file to remove (Inbox dispositions in steps 2–3, superseded canonical PDFs in step 2.5) appends `{drive_file_id, title, reason}` to this same list. Nothing writes its own separate `_processed_*.json`: there is exactly one certification per run (see step 4). That lets `cleanupInbox()` trash everything the run touched in one script execution, instead of the list fragmenting across several ad hoc files.

### 1. Inventory

1. List all children of `Inbox/`. Partition into: PDFs; `LINK_ONLY_*.txt` notes; `_manifest.csv`; `_processed_*.json` (leftover certifications the script hasn't executed yet: treat the files they list as already dispositioned and skip them); and anything else (report as unexpected).
2. Download `_manifest.csv`. It is the provenance record: message ID, sender, subject, original filename, Drive file ID, SHA-256.
3. A manifest row whose `drive_file_id` no longer exists in the Inbox **and** is absent from the intake log means a file vanished without a disposition. Flag it (rows from before the pipeline went live can be ignored, per a note in the registry).

### 2. Per-PDF disposition loop

For each PDF in `Inbox/` (skipping any covered by a leftover `_processed` list):

1. **Hash dedup.** Take the SHA-256 from its manifest row (computed by the script from the raw bytes). No manifest row → download the file and compute the SHA-256 locally. If the SHA-256 already appears in `intake_log.json` → disposition `duplicate`: log it (pointing at the original entry), add the file to the trash list. No copy, no further analysis.
2. **Identify from content.** Read the first 2–3 pages, **not page 1 alone**, and match against the registry: the document must match its store on **≥ 2** of `legal_entity` / `store_code` / `address_fragment` / `bank_accounts`.
   - Page 1 alone is unsafe. FR packets bundle a cover letter ahead of the P&L, a BR may be embedded even deeper in the same bundle (see the store's `br_packaging_note`), and the store code isn't printed on every store's packet, so identity often depends on the address in the P&L header or a bank name that doesn't surface until a later page.
   - Stop scanning once 2 fields are confirmed; don't read past page 3 hunting for a third.
   - Address variants the registry lists for a store count as that store's address.
   - Then classify the type (BR: "Bank Account Reconciliation Worksheet"; GL: "General Ledger" with account-by-account detail; FR: cover letter / P&L / comparative income report) and take the period from the header. The period is usually on whichever page carries the P&L or GL header, not necessarily page 1.
3. **Provenance check.** Confirm the manifest sender (or the paired LINK_ONLY body) traces to the store's `accountant.email_sender`. A document with no accountant provenance still files if its content identifies it, but gets a report line.
4. **Identification fails** (no store on 2 fields, type unclassifiable, or unreadable): copy it to `Quarantine/` keeping its Inbox name, create `Quarantine/{name}.reason.txt` stating why, log disposition `quarantined`, and add the original to the trash list.
5. **Destination check.** Ensure `Stores/SXX/YYYY-MM/` exists (create it if missing; never guess at names: `SXX` comes from the registry, `YYYY-MM` from the document header). Then:
   - **Slot empty** (no `SXX_{type}_{YYYY-MM}.pdf` in the month folder): copy the PDF there under that canonical name. Disposition `filed`. Add the original to the trash list.
   - **Several bank accounts:** a store with more than one entry in `bank_accounts` has one BR slot per account, named `SXX_BR{n}_{YYYY-MM}.pdf`, where `n` is the account's position in the registry list (1-based). Decide which account a BR belongs to from the account line printed on it (the longest registry entry it contains) and record it as `account` in the intake log. A second account's BR is a different slot, never a supersede. If the account can't be resolved, quarantine the BR with the reason.
   - **Slot occupied, same SHA-256:** should have been caught in step 2.1; if reached anyway, treat it as a duplicate.
   - **Slot occupied, different content: SUPERSEDE.**
     1. Ensure `Stores/SXX/YYYY-MM/superseded/` exists.
     2. Copy the **old** canonical file into it as `SXX_{type}_{YYYY-MM}_superseded_{YYYYMMDD}.pdf`.
     3. Copy the **new** file to the canonical name.
     4. Add the old canonical file to the trash list (reason `superseded_old_canonical`; the script verifies it's inside the project tree).
     5. Log both dispositions: `superseded-old` and `superseded-new`.
     6. If that month already has a report, stamp the month **REANALYZE**: rerun the affected checks and produce a `_rev2` report whose first line states what changed.
6. **Period sanity.** A period more than 3 months old, or in the future: file normally, but add a report line: "late/odd-period arrival: why now?"

Every store files identically regardless of tier (active, light, or filed-only). Phase 1 never analyzes anything; it only files. Count stores that have never received a packet for the filed-only summary line. Whether a filed document gets a full review, a light "notable items" pass, or no review at all is decided in Phase 2.

### 3. LINK_ONLY notes

For each `LINK_ONLY_*.txt`: download and read it. Report the sender, subject, and a body summary; it is the evidence that distinguishes a stripped forward from a packet that was never sent. If the body references documents (e.g., download links), flag it for human follow-up and add it to the chase email. Log disposition `link_only-logged` and add the note to the trash list.

### 4. Certify cleanup: ONE file per run

**Standalone run** (`process-inbox` on its own): write the run-scoped trash list now as `Inbox/_processed_{YYYYMMDD_HHMM}.json`:

```json
{
  "written_by": "agent sweep",
  "sweep_date": "YYYY-MM-DDTHH:MM",
  "trash": [
    {"drive_file_id": "…", "title": "…",
     "reason": "filed|duplicate|quarantined|link_only_logged|superseded_old_canonical"}
  ]
}
```

**Front half of the full monthly cycle** (`analyze-month`): do **not** write yet. Carry the list forward into Phase 2 exactly as it is. Phase 2 appends its own entries to the same list, and the single combined certification is written once, at the true end of the run. (Writing a separate certification every time a new cleanup need surfaced does work, because the script batches every `_processed_*.json` it finds, but one logical run should produce one certification.)

**Failure fallback:** if a later phase fails or aborts before the combined certification would be written, write it anyway with whatever the list holds. Accumulated trash must never silently vanish because a downstream step errored.

Only list files whose disposition is **complete** (the copy has been verified by re-listing the destination). Never list `_manifest.csv`. The script trashes exactly these files on its next run, and Drive trash keeps a 30-day recovery copy.

### 5. Completeness matrix

For each active store × each unclosed month: are FR, GL, and one BR per registry `bank_accounts` entry all filed?

- Expected date = month-end + the store's delivery lag (the registry's `delivery_lag_days`, kept in line with the median of the store's observed lags); grace = 5 days.
- Past grace and still missing → CHASE flag naming exactly what's missing. Batch every gap into **one** drafted email, since one accountant serves every store.
- If mail reaches the Inbox by forwarding, a CHASE first asks whether the email was received and forwarded at all.
- Partial packets: the review runs anyway, the report is stamped INCOMPLETE, and the month stays in open items.

### 6. Record and report

1. Append every disposition to `intake_log.json`: SHA-256, received timestamp, source message ID, original filename, identified store / type / period, disposition, final path, sweep date. **Append-only; never rewrite history.**
2. Write the intake summary: cleanup errors (if any) → completeness matrix → dispositions table → filed-only count line → new or unexpected items → CHASE flags.
3. Hand off to Phase 2 for every active store whose packet is now complete.

## First sweep on an existing archive

If documents were filed by hand before the pipeline existed, seed their hashes into `intake_log.json` during the first sweep (download each archived PDF, compute its SHA-256, and add an entry with disposition `seeded-from-archive`). Otherwise a later re-forward of an already-archived month would be filed again instead of being recognized as a duplicate.
