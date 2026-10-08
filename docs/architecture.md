# Architecture

The system has two halves with a strict contract between them. The **Apps Script** is the "dumb half": it moves files and executes lists. Its one decision is the sender gate: who is allowed to put anything in the Inbox at all. The **agent** is the half that decides everything else: what a document is, where it goes, and what it means. Every stage can be re-run safely, nothing is deleted without a certified list, and no filed document is replaced without a person's approval.

## System overview

```mermaid
flowchart LR
    subgraph G["Google Workspace"]
        GM["Gmail<br/>forwarded accountant mail"] --> AS["Apps Script<br/>every 30 min<br/>sender gate"]
        AS --> IN[("Drive Inbox/<br/>PDFs + _manifest.csv")]
        AS -. "refused" .-> RJ[("Rejected/<br/>headers-only notes")]
    end

    subgraph AG["Agent layer (Claude skills)"]
        SW["Sweep<br/>identify · dedupe · file"]
        RV["Review subagents<br/>one per store, in parallel"]
        RP["Portfolio report<br/>+ chase email"]
        SW --> RV --> RP
    end

    subgraph ST["Claude Project state"]
        REG[("stores_registry.json")]
        LOG[("intake_log.json<br/>append-only")]
        BL[("baselines/")]
    end

    subgraph DR["Drive archive"]
        FILED[("Stores/ · Quarantine/")]
        REPORTS[("Reports/")]
    end

    IN --> SW
    REG --> SW
    SW --> LOG
    SW --> FILED
    SW -- "_processed_*.json" --> AS
    FILED --> RV
    RV <-- "read, write, verify" --> BL
    RP --> REPORTS
```

## Google Drive folder tree

The Apps Script's `setupDrive()` builds this once, and every later lookup finds folders by name, so no folder IDs are stored anywhere.

```
PnLAnalyze/
├── Inbox/                      the script writes here; the agent sweeps it
│   ├── 20260908_c1e15fbd_Riverside_Aug2026_Financials.pdf
│   ├── LINK_ONLY_20260913_dae9c274.txt
│   ├── _manifest.csv           one row per saved file (with SHA-256) or rejected email; never deleted
│   ├── _processed_{stamp}.json cleanup list written by the agent, executed by the script
│   └── _cleanup_error_{stamp}.txt  written by the script if a cleanup fails
├── Stores/
│   └── S01/2026-08/            the archive: S01_FR_2026-08.pdf, S01_GL_…, S01_BR_…
│       ├── pending/            corrections waiting for approval: SXX_FR_YYYY-MM_pending_{sha8}.pdf
│       ├── approved/           a person moves a pending correction here, then runs approveCorrections()
│       └── superseded/         earlier copies replaced by an approved correction, date-stamped
├── Quarantine/                 documents that failed the identity or sender rule, each with a .reason.txt
├── Rejected/                   REJECTED_SENDERS_{stamp}.txt: one headers-only summary per run of refused mail
├── _approved_corrections.json  the script's mirror of the approval record (Script Properties); read by the sweep
├── Reports/                    YYYY-MM_portfolio_report.md, one per month
├── _intake_seen_message_ids.json  the script's processed-message memory (most recent 10,000 IDs)
└── Portfolio/                  dashboards and cross-store views
```

The state the agent reads and writes every run (registry, intake log, baselines) lives with the skills in a Claude Project, not in Drive.

## One monthly run

```mermaid
sequenceDiagram
    autonumber
    participant AS as Apps Script
    participant I as Drive Inbox
    participant O as Orchestrator
    participant L as Intake log
    participant B as Baselines
    participant A as Review subagents

    alt sender allowed and authenticated
        AS->>I: save PDFs as {date}_{msgId}_{name}, append _manifest.csv rows
    else refused
        AS->>I: append a rejected_sender row (the note goes to Rejected/, nothing to Inbox/)
    end
    O->>L: load registry + intake log (SHA-256 index)
    O->>O: apply corrections in approved/ whose hash the script recorded as approved (supersede, then re-analyze)
    O->>I: read _manifest.csv
    loop every file
        alt SHA-256 already in log
            O->>O: mark duplicate
        else new file
            O->>I: read first 3 pages only
            alt 2+ identity fields, known doc type, sent by that store's accountant
                alt slot empty
                    O->>I: copy to Stores/SXX/YYYY-MM/SXX_{type}_YYYY-MM.pdf
                else slot already filed
                    O->>I: copy to Stores/SXX/YYYY-MM/pending/ (report: pending approval)
                end
            else uncertain, or unexpected sender
                O->>I: copy to Quarantine/ with a reason
            end
        end
        O->>L: append disposition
    end
    O->>B: snapshot all baselines to rollback
    par each active store with documents
        O->>A: store slice (registry entry, files, tier)
        A->>B: read own baseline
        A->>A: cross-checks, guardrails, anomalies, open items
        A->>B: write complete baseline, re-read, validate
        A-->>O: structured result + write_confirmed
    end
    O->>O: reconcile writes, graduate light-tier stores
    O->>O: one portfolio report + chase email
    O->>I: write _processed_{stamp}.json (once)
    AS->>I: next run trashes the listed files (after safety checks)
```

## Components

| Component | Where | Responsibility |
|---|---|---|
| Intake script | [`apps-script/Code.gs`](../apps-script/Code.gs) | Refuses mail that isn't from an allowed, authenticated sender (headers-only note + manifest row), saves PDFs with provenance names, writes `_manifest.csv` (with SHA-256), labels threads, remembers processed message IDs, writes `LINK_ONLY` notes, executes cleanup lists with safety checks, builds the Drive tree |
| Sweep | [`agent/skills/process-inbox`](../agent/skills/process-inbox/SKILL.md) · [`sweep_procedure.md`](../agent/procedures/sweep_procedure.md) | One disposition per Inbox file, completeness matrix, one cleanup certification; never extracts figures |
| Review | [`monthly_analysis_procedure.md`](../agent/procedures/monthly_analysis_procedure.md) | One store, one baseline, one structured result |
| Orchestrator | [`agent/skills/analyze-month`](../agent/skills/analyze-month/SKILL.md) | Sequencing, parallel fan-out, write reconciliation, tier graduation, the consolidated report |
| Registry validator | [`tools/validate_registry.js`](../tools/validate_registry.js) | Catches registry mistakes (duplicate IDs, stores that can't meet the identity rule) before upload, and prints the `ALLOWED_SENDERS` value |
| Demo mode | [`src/pnl_analyzer/`](../src/pnl_analyzer/) | Deterministic reference implementation of the sweep and review, run on synthetic data |

## Contracts

**`_manifest.csv`** (script → agent), one row per saved file:

`received_ts, gmail_message_id, from_addr, subject, original_filename, drive_file_id, sha256, note`

`note` is empty for a saved PDF, `link_only` for an email with no PDF, and `rejected_sender: <reason>` for refused mail. A rejected row's `original_filename` and `drive_file_id` point at its note in `Rejected/`, and it has no hash.

## Who may send documents

Two checks, one in each half:

1. **Apps Script sender gate** (before anything is saved). The bare address from the `From` header (the address in the final `<…>`, never the display name, compared without case) must be on the `ALLOWED_SENDERS` Script Property. The registry's `accountant.email_sender` is the source of truth; the script can't read the registry, so the value is copied by hand (`tools/validate_registry.js` prints it). The message must also authenticate, judged from Gmail's own `Authentication-Results` header (authserv-id `mx.google.com`): a DMARC result decides on its own (only `pass` is accepted); with no DMARC result, a DKIM or SPF pass counts only if its domain aligns with the sender's; no header is a failure. An allowed address that fails authentication is treated exactly like an unlisted one. Refused mail is never dropped silently: each refused email gets a manifest row marked `rejected_sender` and an entry (sender, recipients, subject, message ID, reason, attachment names; never the body or attachments) in the run's one summary note, `Rejected/REJECTED_SENDERS_{stamp}.txt`. Every refused message is labeled `pnl/refused`, which both searches exclude, so a spam burst costs one note per run and is never fetched again. The sweep logs it as `rejected-sender-logged` and the report lists it under *Needs attention*. A refused email with no attachments at all creates nothing (a log line only): there is no document to account for, and a plain-text spam burst must not fill Drive with notes. The 7-day catch-up search, the one that finds link-only emails because it has no attachment filter, is limited to `from:(<ALLOWED_SENDERS>)`, so strangers' attachment-less mail isn't even fetched; the no-attachment rule is the backstop. One run writes at most 10 link-only notes; past that it logs a warning and leaves the rest for the next run. The script refuses to run at all until `ALLOWED_SENDERS` is set.
2. **Sweep provenance check** (after the content identifies a store). The manifest sender must be that store's registered `accountant.email_sender`. If it isn't, the document is quarantined with a reason, however convincingly it identifies the store. This catches anything that got past the gate, such as a second allowed address used for the wrong store.

Note that a gate on the sender means mail must arrive *from* the accountant: a message a person forwards by hand arrives from that person and is refused. Use a Gmail filter that forwards automatically, or have the accountant send to the intake address directly.

## Corrections need approval

A document whose canonical slot (`SXX_{type}_YYYY-MM.pdf`) is already filed with different content is a correction, and a correction never replaces a filed document on arrival:

1. The sweep copies it to `Stores/SXX/YYYY-MM/pending/SXX_{type}_YYYY-MM_pending_{sha8}.pdf`, logs `correction-pending` with the canonical path it would replace, and certifies the Inbox copy for cleanup. The review keeps using the filed document, and the report shows the correction as **pending approval** (at the top, and in the store's section) on every run until it is approved. The chase email doesn't ask the accountant to fix a tie-out break the pending correction already addresses.
2. A person approves it in two steps: they move the file from `pending/` into `approved/` beside it, then run `approveCorrections()` in the Apps Script editor. That records the file's SHA-256 in Script Properties. The move alone approves nothing: the agent can write to Drive (often as the same Google account), so a file in `approved/` could have been put there by a misled agent. Script Properties are out of the agent's reach. Each run, the script mirrors the record to `_approved_corrections.json` in the root folder for the sweep to read. If that file holds a hash the record lacks, the script restores it and leaves `Inbox/_integrity_alert_{stamp}.txt`, which the next sweep reports first.
3. The next sweep, before it touches the Inbox, applies each file in `approved/` whose SHA-256 is in the approval record **and** matches a logged pending correction. A file in `approved/` without a record is reported every sweep ("in approved/ without an approval record") and never applied. For an approved file: the old canonical file is copied to `superseded/`, the approved file takes the canonical name, both dispositions are logged (`superseded-old`, `superseded-new`), the old canonical file is certified for cleanup (the script trashes it only once it sees the identical backup **and** that the file now holding the canonical name has an approved hash, so even a sweep that skipped the check can't remove the original), and the month is re-analyzed. The approved copy stays in `approved/` as the record of the approval. A file in `approved/` that matches no pending correction replaces nothing and is reported.

**`_processed_{stamp}.json`** (agent → script), written once per run:

```json
{ "written_by": "agent sweep", "sweep_date": "2026-09-15",
  "trash": [ { "drive_file_id": "…", "title": "…", "reason": "filed|duplicate|quarantined|link_only_logged|pending_approval|superseded_old_canonical" } ] }
```

Before it is written, every entry passes the certification check (see [Untrusted content](#untrusted-content)). The script trashes a listed file only if it is not a folder, sits in exactly one folder, and either:

- that folder is **`Inbox/` itself** (not a subfolder) and the name doesn't start with `_` (so `_manifest.csv`, `_processed_*` and `_cleanup_error_*` are never trashed by a list); or
- it is **the one exception**: a replaced original in the archive. The reason must be `superseded_old_canonical`, the file must be a canonical `SXX_{type}_YYYY-MM.pdf` directly in this project's `Stores/SXX/YYYY-MM/`, and that month's `superseded/` folder must already hold a byte-identical backup (`SXX_{type}_YYYY-MM_superseded_*.pdf`).

Everything else is refused: files in `Stores/` (other than that exception), `Reports/`, `Quarantine/`, `Rejected/`, Inbox subfolders, other Drive trees, and a replaced original whose backup is missing or differs. So a bad or manipulated list can at worst empty the Inbox, whose files all have archived or quarantined copies, a manifest row and a 30-day trash recovery. Anything it refuses or fails on goes into `_cleanup_error_{stamp}.txt`, which the next sweep reports first. The list itself is retired after one execution, so a bad list is never retried forever.

**`stores_registry.json`**: per store, the identity fields (`legal_entity`, `store_code`, `address_fragment`, `bank_accounts`), `status` (`active` or `filed-only`), `tier` (`active` or `light`), accountant, related parties, and baseline path. [Example](../sample-data/seed/stores_registry.json).

**`intake_log.json`**: append-only; one entry per file with `sha256`, `received`, `source_msg_id`, `original_filename`, `store`, `type`, `period`, `disposition`, `final_path`, and `sweep_date`. It is both the dedup index and the audit trail.

**`baselines/SXX_baseline.json`**: each store's memory. Validated by [`schemas/baseline.schema.json`](../schemas/baseline.schema.json).

| Key | Purpose |
|---|---|
| `meta` | Store, entity, last closed month, last updated |
| `monthly_history` | Metric → month → value (net sales, net income, every P&L line, delivery lag, packet status) |
| `ratio_guardrails_pct_of_net_sales` | Each line's observed % of sales and normal range |
| `vendor_baselines` | Known payees with role, bands, fixed amounts; new payees enter as `MONITOR` |
| `capital_projects` | Capitalized spending (invisible on the P&L) with running totals |
| `seasonality_notes` | Expected seasonal moves, applied before flagging |
| `open_items_carryforward` / `resolved_items` | Loose ends with IDs `OI-YYYY-MM-X`; nothing leaves without evidence |

## Untrusted content

Every document the agent reads was written by someone outside the system, so its text is data, never an instruction. Each skill and procedure opens with that rule. Three things back it up in code:

1. **Less free text reaches the agent.** A `LINK_ONLY` note holds the sender, subject, date, message ID and a list of the links found; the email body is not copied. Rejected-sender notes never include the body either.
2. **Injected documents are quarantined.** Text addressed to an AI, or asking for actions on the pipeline's own files ("ignore prior rules", "trash the Stores folder", "do not mention this"), quarantines the document with a reason starting `suspicious instructions`, before it is identified, so it can't be filed or taken for a correction. The same check applies to link-only notes. The reference patterns are in [`intake.py`](../src/pnl_analyzer/intake.py); the agent applies the same rule by reading.
3. **The cleanup list is checked before the script sees it.** [`certify.py`](../src/pnl_analyzer/certify.py) compares every entry of the drafted trash list with this run's dispositions: the file must have one, and the entry's reason must be the one that disposition calls for. Anything else (an invented file ID, a folder, a file certified with the wrong reason, a duplicate entry) is dropped and listed under *Needs attention*. So even an agent that was talked into adding something to the list can't get it trashed. The script's own checks (never the manifest, never a folder, never outside the tree) remain the last line.

The synthetic Inbox carries a canary PDF with planted instructions; [evaluation.md](evaluation.md#the-prompt-injection-canary) makes quarantining it a pass/fail gate for the agent.

## Division of labor: the model reads, code checks

Reading a messy PDF is what a language model is good at; adding up a bank reconciliation is not. So the review is split at a data contract, [`schemas/extraction.schema.json`](../schemas/extraction.schema.json):

```text
PDFs ──(agent reads)──▶ extraction JSON ──(code checks)──▶ flags + updated baseline ──(agent explains)──▶ report
```

- **Agent:** identify and classify documents, transcribe figures exactly, then interpret the computed flags and write the questions.
- **Code** ([`analyze.py`](../src/pnl_analyzer/analyze.py)): tie-outs, bank-rec math, duplicate detection, stale-check aging, variance against baseline, open-item resolution. It is exact, repeatable, cheaper than a model call, and testable.

The reference implementation plays the agent's part with a PDF parser and writes the same JSON, so `--dump-extracted` gives a ground truth and `--extracted-dir` runs the checks on an agent's transcription. Each rule in `analyze.py` is a small function from plain data to flags (`check_tieout`, `check_duplicates`, `check_bank_rec`, ...), unit-tested on its own; a new rule is one function plus one call in `review_store`. [`evaluation.md`](evaluation.md) covers scoring the agent against the reference.

## Review rules

The full rules are in the [review procedure](../agent/procedures/monthly_analysis_procedure.md). In summary:

| Check | Rule |
|---|---|
| Completeness | P&L + GL + one BR per registered bank account. A partial packet is reviewed but the month isn't closed. With several accounts each BR gets its own slot (`SXX_BR1_…`, `SXX_BR2_…`), chosen from the account line printed on it, and each is reconciled separately. |
| Delivery lag | Packet completion date − month-end. **Late** once it exceeds the store's median lag + 5 days. |
| Cross-checks | BR adjusted book balance = GL cash; unreconciled amount = 0; P&L lines = GL account totals; management fee and royalty match their contractual rules. |
| Guardrails | Each ratio against its normal range, with the dollar impact of any breach; 3 months drifting the wrong way is a MONITOR flag. |
| Seasonality | Applied before anything is flagged. |
| Transactions | Vendor bands, new payees, missing recurring charges, fixed-amount drift, lumpy repair accounts, capitalized purchases, duplicates, round numbers, distributions. |
| Standing red flags (every tier) | BR unreconciled · management fee off-rule · new vendor over $1,000 · utility over 1.5× its median · open checks over 90 days · payroll drift over 15% · inventory adjustments over 1 point · cash short/over beyond ±$100 |

## Failure handling

| Situation | Behavior |
|---|---|
| Two script runs overlap | A script lock makes the second run exit immediately. |
| Saving the processed-message memory fails | The ID file is written and re-read before any thread is labeled. On failure the run logs the error and stops with nothing labeled; the next run retries. The worst case is a PDF saved twice, which the sweep skips by SHA-256. An unreadable ID file stops intake rather than re-saving every recent email. |
| Resend lands in an already-processed thread | The catch-up query (allowed senders only) sees it; message-ID memory stops old messages being saved twice. |
| Backlog of more than 100 threads | Searches page through every result (up to 5,000 threads), so one run clears it. Past a 4.5-minute budget the run stops taking new threads and logs a warning; the rest are left unlabeled for the next run. |
| Manifest row whose file name has a path separator or `..` | Rejected before any path is built (`invalid-manifest-row`), reported, never opened, filed or certified. |
| Burst of junk mail | Attachment-less mail from anyone not allowed creates nothing. Notes are capped at 10 per run, with a warning in the log; the rest wait for the next run. |
| Mail from an unlisted address, or failing SPF/DKIM/DMARC | Nothing saved to the Inbox. A manifest row, an entry in the run's one `REJECTED_SENDERS` summary note in `Rejected/`, a line in the report, and a `pnl/refused` label so the message is never fetched again. |
| A file appears in `approved/` that no person approved | Not applied: the sweep needs its hash in the approval record, which only `approveCorrections()` writes. Reported every sweep. A forged entry in the Drive mirror is undone by the script and reported as an integrity alert. |
| Document identifies a store but came from someone other than its accountant | Quarantined with the reason. Never filed on content alone. |
| PDF over a parsing limit (20 MB, 100 pages, or 20 s of parsing) | Quarantined at intake with the limit named in the reason, before it is parsed (size is checked without opening the file). A filed document that hits the time limit at review is reported as not extracted, with the reason, and the month stays open. Tables are only read from pages with ruling lines. |
| Document can't be identified | Quarantined and listed in the report. Never guessed. |
| Same file arrives twice | Skipped by SHA-256. The duplicate is logged. |
| Month rerun (new documents, or a retry) | The review first undoes its own earlier effects on that month (open items it opened, items it resolved, vendors it first saw), compares only against earlier months, and keeps the rollback snapshot from the first run, so a rerun gives the same result as the first run would have with the same documents. |
| Corrected document arrives | Held in `pending/`; the filed document stays in use and the report shows the correction as pending approval. Once a person moves it into `approved/` and runs `approveCorrections()`, the next run gives it the canonical name, keeps the old copy in `superseded/`, certifies the old canonical file for trash (trashed only once its backup is confirmed), and re-analyzes the month (an already-reported month's report is reissued as `_rev2`; the earlier one is kept). |
| Document text addresses an AI or asks for actions | Quarantined as `suspicious instructions`, flagged at the top of the report, and never filed or obeyed. Nothing else in the run changes. |
| Cleanup list names a file this run didn't disposition, or with the wrong reason | The entry is dropped before the certification is written and listed under *Needs attention*. |
| Email has no attachment | The `LINK_ONLY` note (metadata and links only, no body) is read, logged, and certified for cleanup; any links in it are flagged for a person, and the chase email asks for the PDF. |
| First month, or zero sales | A store with no history has nothing to compare against, so variance is skipped, the standing red flags still run, and the month becomes the baseline. A month with zero net sales is flagged high; percent-of-sales checks are skipped and the month is left out of later percent statistics. |
| An extraction leaves out a document that was filed | Completeness is judged from what intake filed, so an extraction JSON that omits the P&L, the GL or a bank rec (or reads nothing from it) cannot close the month. The packet is marked incomplete, the checks that needed the document are reported as not run, and an open item says which document to extract. The accountant is not asked for something they already sent. |
| Packet incomplete | Available documents are reviewed and the section is stamped INCOMPLETE. The month stays open, with an open item and a chase line. |
| Evidence for an open item is in a missing document | Carried forward as "cannot verify this month"; never auto-resolved. |
| Baseline write fails verification | The orchestrator attempts a fallback write; if that fails, the store is reported as not closed at the top of the report. The rollback snapshot is intact. A light-tier store is only promoted to active after its baseline write is confirmed. |
| Cleanup fails or is refused | The script writes `_cleanup_error_{stamp}.txt`; the next sweep surfaces it first. Refused: anything not directly in `Inbox/`, except a replaced original whose identical backup is in `superseded/`. Trashed files are recoverable for 30 days. |
| Run crashes midway | The certification is still written with whatever was fully dispositioned, and baselines can be restored from the rollback snapshot. |

## Configuration and secrets

The script asks for exactly three OAuth scopes, listed in [`appsscript.json`](../apps-script/appsscript.json): `gmail.modify` (read mail, apply the label; Gmail goes through the Advanced Gmail service because `GmailApp` would need full mailbox control), `drive` (`drive.file` can't see the files the agent writes, which cleanup and the hash migration must read), and `script.scriptapp` (the trigger). It is meant to run in a dedicated Google account that only receives the accountant's mail, with the project folder shared to the main account; the [README](../README.md#permissions) explains each scope.

The intake script reads its environment from **Script Properties** (Project Settings → Script Properties), never from code:

| Property | Required | Default |
|---|---|---|
| `INTAKE_ADDRESS` | yes | none (the dedicated address accountant mail is forwarded to) |
| `ALLOWED_SENDERS` | yes | none (the accountant's address(es), comma separated, copied from the registry's `accountant.email_sender`) |
| `PROCESSED_LABEL` | no | `pnl/ingested` |
| `ROOT_FOLDER` | no | `PnLAnalyze` |

The script's processed-message-ID memory is not a Script Property, because a property value is capped at 9 KB (a few hundred IDs). It lives in `_intake_seen_message_ids.json` in the root folder, outside the Inbox, so no cleanup list can trash it. The first run of the current version moves any IDs left in the old `PNL_PROCESSED_MSG_IDS` property into the file and deletes the property. clasp's credential file (`.clasprc.json`) and project file (`.clasp.json`) are excluded by `.gitignore`.
