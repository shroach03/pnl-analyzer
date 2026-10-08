# Intake script (Google Apps Script)

`Code.gs` is the "dumb half" of the pipeline. It runs every 30 minutes and makes one decision, who may send documents; everything else is the agent's:

1. **Sender gate.** Only mail whose bare `From` address (not the display name; case doesn't matter) is on `ALLOWED_SENDERS` **and** which passes Gmail's authentication check gets in. Gmail's own `Authentication-Results` header decides: DMARC `pass` is required when there is a DMARC result; otherwise a DKIM or SPF pass aligned with the sender's domain. Anything else, including the accountant's address failing authentication, is refused: it gets a manifest row with the note `rejected_sender: <reason>` and an entry in that run's **one** summary note, `Rejected/REJECTED_SENDERS_<stamp>.txt` (headers only, never the body or attachments; up to 200 entries, then a count), and nothing is saved to `Inbox/`. A refused email with **no attachments at all** creates nothing (one log line). Every refused message is labeled `pnl/refused` (Script Property `REFUSED_LABEL` to rename it), and both searches exclude that label, so spam is looked at once and never fetched again. The label goes on the message, not the thread, so a stranger's reply on the accountant's thread doesn't hide the accountant's later mail.
   - **Two searches.** The main search finds unprocessed threads with attachments. The 7-day catch-up search, which finds resends inside already-labeled threads and the link-only emails the main search can't see, is limited to `from:(<ALLOWED_SENDERS>)`, so other people's attachment-less mail is never fetched. A link-only email from an allowed, authenticated sender still gets its `LINK_ONLY` note.
   - **Note cap.** One run writes at most 10 `LINK_ONLY` notes (`CONFIG.MAX_NOTES_PER_RUN`); refused mail shares the one summary note. Past that it logs a `WARNING: note cap … reached` line and leaves the remaining messages unmarked, so the next run picks them up and nothing is lost. PDFs are never capped.
   - **Pagination and time budget.** Each search pages through Gmail 100 threads at a time (up to 5,000), so a backlog clears in one run. The run stops taking new threads after 4.5 minutes, inside Apps Script's 6-minute limit, and logs `WARNING: time budget reached …`; whatever is left stays unlabeled and unremembered, so the next run picks it up.
2. Finds forwarded accountant emails and saves each PDF attachment to `Inbox/` as `{yyyyMMdd}_{msgId[-8:]}_{original_name}`, so names never collide and every file traces back to its email.
3. Appends a row per file to `Inbox/_manifest.csv` (received time, message ID, sender, subject, original name, Drive file ID, SHA-256).
4. Remembers every processed message ID in `<ROOT>/_intake_seen_message_ids.json` (the most recent 10,000), then labels the thread, so nothing is saved twice, even when a resend arrives as a reply in an already-processed thread. The ID file is written and re-read **before** any thread is labeled; if that fails, the run logs the error and stops with nothing labeled, so mail is never marked done without being remembered.
5. Writes a `LINK_ONLY_<date>_<id>.txt` note (sender, subject, date, message ID and the links found; never the email body, which the agent would otherwise read) when an email has no PDF, so it's never silently lost.
6. Executes the agent's `_processed_*.json` cleanup lists: trashes each listed file after safety checks, and writes `_cleanup_error_*.txt` for anything it refuses or can't do.

| Function | Use |
|---|---|
| `setup()` | Run once. Builds the Drive folder tree, the Gmail label, the manifest, and the 30-minute trigger. |
| `setupDrive()` | Builds (or repairs) just the folder tree. Safe to re-run. |
| `processInbox()` | The scheduled entry point. Also safe to run by hand. |
| `cleanupInbox()` | Executes pending cleanup lists right away (e.g., straight after an agent sweep). Before trashing an Inbox PDF it checks the evidence itself: a byte-identical copy elsewhere in the project folder (filed, quarantined, pending), or an earlier manifest row with the same SHA-256 (duplicate). |
| `approveCorrections()` | **Run by you**, after moving a held correction from `pending/` into `approved/`. Records the SHA-256 of every PDF in an `approved/` folder in the `PNL_APPROVED_CORRECTIONS` Script Property, logs each file so you can check it's the one you meant, and refreshes `_approved_corrections.json`, which the sweep reads. The agent can't write Script Properties, so a file it puts in `approved/` is never applied. |
| `revokeApprovals()` | Withdraws every recorded approval (e.g. if `approveCorrections()` logged a file you didn't move). |
| `migrateSeenIdsToFile()` | One-time: moves the processed-message IDs from the old `PNL_PROCESSED_MSG_IDS` Script Property into the ID file, verifies it, and deletes the property. `processInbox()` also does this by itself on its first run, so running it by hand is optional. |
| `pauseIntake()` / `resumeIntake()` | Turn the schedule off and on. Unprocessed mail just waits. |

## Setup

1. Ideally signed in as a dedicated Google account that only receives the accountant's mail (see [Deploy the intake script](../README.md#deploy-the-intake-script)), create an Apps Script project (script.google.com) and add `Code.gs` and `appsscript.json`. `appsscript.json` enables the Advanced Gmail service and lists the exact OAuth scopes (`gmail.modify`, `drive`, `script.scriptapp`; [why each](../README.md#permissions)). If you paste the files in rather than pushing with `clasp`, also add **Gmail API** under **Services**.
2. **Project Settings → Script Properties**:

   | Property | Required | Default |
   |---|---|---|
   | `INTAKE_ADDRESS` | yes | none. The address accountant mail arrives at: the dedicated account's address (or a plus-address such as `you+pnl@gmail.com` if you run it in your own account) |
   | `ALLOWED_SENDERS` | yes | none. The accountant's sender address(es), comma separated. The registry's `accountant.email_sender` is the source of truth; the script can't read the registry, so copy the value by hand (`node tools/validate_registry.js <registry>` prints it). Update it whenever the registry's sender changes. |
   | `PROCESSED_LABEL` | no | `pnl/ingested` |
   | `ROOT_FOLDER` | no | `PnLAnalyze` |

3. Run `setup()` and approve the permissions. It refuses to run until `INTAKE_ADDRESS` and `ALLOWED_SENDERS` are both set. On an existing deployment, run `setupDrive()` once to add the `Rejected/` folder (the script also creates it the first time it refuses a sender).

The gate checks who *sent* the email. Mail auto-forwarded by a Gmail filter keeps the accountant's `From`, and passes authentication when the accountant's mail is DKIM-signed (forwarding breaks SPF, not DKIM). An email a person forwards by hand arrives from that person and will be refused.

**Upgrading to the Advanced Gmail service.** This version reads mail through `Gmail.Users.*` instead of `GmailApp`, and asks for narrower permissions. After deploying it, run `setup()` by hand once and approve the new permission screen; until then the scheduled trigger fails. The `pnl/ingested` label and the processed-message memory carry over unchanged.

Nothing environment-specific is in the code, so the same file runs in any Google account.

## Upgrading from the Script Property message memory

Older versions kept the processed-message IDs in one Script Property, `PNL_PROCESSED_MSG_IDS`. A property value is capped at 9 KB, which holds only a few hundred IDs, so the list could stop saving as it grew. This version keeps them in `_intake_seen_message_ids.json` in the root folder. To upgrade, deploy the new `Code.gs`, then either run `migrateSeenIdsToFile()` once or let the next scheduled `processInbox()` do it. Either way the IDs are merged into the file, and the file is re-read to confirm every ID is there. Only then is the property deleted. The log line `Moved N processed-message ID(s) …` confirms it, and afterwards the property no longer appears under **Project Settings → Script Properties**.

Don't delete or hand-edit the ID file. If it becomes unreadable, intake stops with an error rather than re-saving every recent email; restore an earlier version from the file's Drive version history.

## Working with clasp

```bash
npm install -g @google/clasp
clasp login                               # creates ~/.clasprc.json (never commit it)
clasp clone <SCRIPT_ID> --rootDir apps-script
```

`.clasp.json` (which holds the script ID) and `.clasprc.json` are both in `.gitignore`.

## Upgrading an existing deployment from MD5 to SHA-256

Older versions hashed files with MD5 (the manifest column was `md5`). This version writes `sha256`. An existing manifest and the agent's `intake_log.json` must be converted once, or a re-sent file would no longer be recognised as a duplicate. The conversion runs inside Drive, so no real files leave it and nothing is written to this repository.

**Before step 1**, check **Project Settings → Script Properties** in the project you are running. `ROOT_FOLDER` must be your Drive folder's exact name (the default is `PnLAnalyze`); the migration functions need nothing else. If the properties are empty, you are in a different project from the one that ran your intake, so set `ROOT_FOLDER` (and `INTAKE_ADDRESS` too, before you ever run `setup()` or `processInbox()`). Don't run `setup()` until `ROOT_FOLDER` is right, because setup creates the folder tree under whatever name is set.

| # | Do this | What it does |
|---|---|---|
| 1 | Run `pauseIntake()` | Stops the timer, so nothing appends to the manifest mid-migration. |
| 2 | Deploy this version of `Code.gs` | |
| 3 | Run `previewSha256Migration()` | Hashes every PDF in `Stores/`, `Quarantine/` and `Inbox/`, then writes `_manifest_sha256_preview.csv` and `hash_migration_map.json` (md5 → sha256 pairs only) to the root folder. The live manifest is untouched. Read the log line: it reports how many rows mapped and how many are unmapped. |
| 4 | Convert the intake log (below) | Uses the map from step 3. |
| 5 | Run `applySha256Migration()` | Saves an exact copy as `_manifest_md5_backup.csv` in the root folder, swaps the preview in, and re-reads it to verify. It refuses if the manifest changed since the preview. |
| 6 | Run `resumeIntake()` | |

Manifest rows whose file was never archived (nothing in `Stores/` or `Quarantine/` has that MD5) get a blank hash and a note. Skipped duplicates keep theirs, because they share an MD5 with the copy that was filed. Old rows are never dropped.

**Converting the intake log.** Download `hash_migration_map.json` from the root folder, then, with both files outside this repository:

```bash
python tools/migrate_intake_log.py --log /path/to/intake_log.json --map /path/to/hash_migration_map.json
```

This writes `intake_log.sha256.json` beside the log and leaves the original alone. The script stops without writing anything if an entry's MD5 isn't in the map, and refuses any path inside the repository. Review the result, then replace the log with it (in a Claude Project, upload it in place of the old one, along with the updated procedures from `agent/`).

The sender gate and the migration functions are tested against a fake in-memory Gmail and Drive: `node --test tests/apps_script/*.test.js` (pytest runs them too when Node is installed).
