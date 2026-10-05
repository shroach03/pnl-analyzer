# Intake script (Google Apps Script)

`Code.gs` is the "dumb half" of the pipeline. It runs every 30 minutes and makes no decisions:

1. Finds forwarded accountant emails and saves each PDF attachment to `Inbox/` as `{yyyyMMdd}_{msgId[-8:]}_{original_name}`, so names never collide and every file traces back to its email.
2. Appends a row per file to `Inbox/_manifest.csv` (received time, message ID, sender, subject, original name, Drive file ID, SHA-256).
3. Labels the thread and remembers the message ID, so nothing is saved twice, even when a resend arrives as a reply in an already-processed thread.
4. Writes a `LINK_ONLY_<date>_<id>.txt` note when an email has no PDF, so it's never silently lost.
5. Executes the agent's `_processed_*.json` cleanup lists: trashes each listed file after safety checks, and writes `_cleanup_error_*.txt` for anything it refuses or can't do.

| Function | Use |
|---|---|
| `setup()` | Run once. Builds the Drive folder tree, the Gmail label, the manifest, and the 30-minute trigger. |
| `setupDrive()` | Builds (or repairs) just the folder tree. Safe to re-run. |
| `processInbox()` | The scheduled entry point. Also safe to run by hand. |
| `cleanupInbox()` | Executes pending cleanup lists right away (e.g., straight after an agent sweep). |
| `pauseIntake()` / `resumeIntake()` | Turn the schedule off and on. Unprocessed mail just waits. |

## Setup

1. Create an Apps Script project (script.google.com) and add `Code.gs` and `appsscript.json`.
2. **Project Settings → Script Properties**:

   | Property | Required | Default |
   |---|---|---|
   | `INTAKE_ADDRESS` | yes | none. A dedicated plus-address (e.g. `you+pnl@gmail.com`) that accountant mail is forwarded to |
   | `PROCESSED_LABEL` | no | `pnl/ingested` |
   | `ROOT_FOLDER` | no | `PnLAnalyze` |

3. Run `setup()` and approve the permissions.

Nothing environment-specific is in the code, so the same file runs in any Google account.

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

The migration functions are tested against a fake in-memory Drive: `node --test tests/apps_script/migration.test.js`.
