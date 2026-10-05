# P&L Analyzer

[![CI](https://github.com/shroach03/pnl-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/shroach03/pnl-analyzer/actions/workflows/ci.yml)

**An automated month-end review pipeline for a multi-store restaurant portfolio.** Accountant emails go in; a filed, cross-checked portfolio report comes out, with every flag tied to a dollar amount and a source transaction.

Each month, every store's accountant sends three documents: a **P&L** (financial report), the **general ledger**, and a **bank reconciliation**. Reviewing them by hand means downloading, renaming, filing, checking every packet is complete, comparing each line to prior months, and chasing whatever is missing. It's slow, and the things that matter most (a vendor paid twice, a bank rec that's $45 off, a check that never cleared) are the easiest to miss.

This project automates that loop end to end. It runs in production on Google Apps Script and Claude agents, and ships with a demo mode that reproduces the whole pipeline on synthetic data.

> All company names, people, vendors, banks, and figures in this repository are **synthetic**. See [Data and privacy](#data-and-privacy).

---

## How it works

```mermaid
flowchart LR
    subgraph S1["1 · Intake: Google Apps Script"]
        A["Accountant email<br/>P&L · GL · bank rec"] --> C["Apps Script, every 30 min<br/>saves PDFs + _manifest.csv"]
        C --> D[("Drive Inbox/")]
        C -. "no attachment" .-> E["LINK_ONLY note"] --> D
    end

    subgraph S2["2 · Sweep agent"]
        D --> F{"Identify from<br/>first 3 pages"}
        F -- "2+ identity fields" --> G["Rename + file<br/>Stores/S01/2026-08/"]
        F -- "SHA-256 already seen" --> H["Skip duplicate"]
        F -- "can't identify" --> Q[("Quarantine/")]
        G --> J{"Packet<br/>complete?"}
    end

    subgraph S3["3 · Review agents, one per store, in parallel"]
        J -- "P&L + GL + BR" --> K["Full review<br/>vs store baseline"]
        J -- "partial" --> L["Review what arrived<br/>stamp INCOMPLETE"]
        K --> M["Snapshot baseline to rollback<br/>write + verify new baseline"]
        L --> M
    end

    M --> R["One portfolio report<br/>+ drafted chase email"]
    S2 -. "_processed_*.json<br/>cleanup list" .-> C
```

1. **Intake** ([`apps-script/Code.gs`](apps-script/Code.gs)). A time-driven Apps Script saves PDF attachments from accountant emails into the Drive `Inbox/` with provenance names, logs each file (with its SHA-256) to `_manifest.csv`, and labels the thread. Emails with a portal link instead of an attachment leave a `LINK_ONLY` note so they're never silently lost. The script makes no decisions; it's the "dumb half."
2. **Sweep** ([`agent/skills/process-inbox`](agent/skills/process-inbox/SKILL.md)). An agent gives every Inbox file exactly one outcome (filed, superseded, duplicate, quarantined, or link-only logged), identifying documents by what's *in* them, never by filename. It then writes one cleanup list, which the script executes on its next run.
3. **Review** ([`agent/skills/analyze-month`](agent/skills/analyze-month/SKILL.md)). Each store is reviewed against its own baseline (history, guardrails, vendor bands, seasonality, open items) by its own subagent, so stores run in parallel and never share state. Baselines are snapshotted before the run and verified after every write.
4. **Report.** One consolidated report: what needs attention, a portfolio dashboard, the intake log, a section per store, and a drafted email to the accountant covering everything still missing.

More detail in [docs/architecture.md](docs/architecture.md). The scaling plan is in [docs/roadmap.md](docs/roadmap.md).

---

## Engineering decisions

| Decision | Why |
|---|---|
| **Layered idempotent intake.** A Gmail label for threads, plus a memory of processed message IDs, plus an SHA-256 log of every file ever received. | Re-running anything is always safe. A resend that replies into an already-processed thread is still caught, and a byte-identical file is recognized and skipped, not double counted. |
| **The script is dumb on purpose.** It saves, logs, and executes cleanup lists. All judgment lives in the agent. | The Apps Script is hard to test and runs unattended. Keeping it free of decisions keeps it trustworthy. |
| **Identity comes from document content** and needs 2 of 4 registry fields (legal entity, store code, address, bank). | Filenames and subjects are unreliable, and misfiling one store's ledger under another silently corrupts two baselines. When the match is uncertain, the document is quarantined, never guessed. |
| **Nothing is deleted by the agent.** The sweep copies files and writes a `_processed_*.json` list; the script trashes only listed files, and only after safety checks (never the manifest, never a folder, never outside the project tree). | Every removal is certified, recoverable for 30 days, and failures are written to an error file the next sweep surfaces. |
| **Corrections supersede, they don't overwrite.** A corrected document takes the canonical name; the earlier copy is preserved in `superseded/`, and a month that was already reported is re-analyzed. | Full audit trail, and the report shows *why* the correction mattered (in the demo, the original P&L was $100 off the ledger). |
| **Rollback point before every run, then write-and-verify** for each baseline. | A bad run can be undone, and a store isn't marked done until its baseline has been re-read and validated against a [JSON schema](schemas/baseline.schema.json). |
| **Seasonality is applied before flagging.** | A 20% higher summer electric bill is expected, not an anomaly. Fewer false alarms means real ones get attention. |
| **Adaptive late-packet detection.** | "Late" is judged against each store's own typical delivery lag (median + 5 days), not a fixed deadline. |
| **Two review tiers.** Stores with under 3 months of history get a learn-and-store pass and graduate to a full review automatically. | You can't judge variance without a baseline, so review depth scales with history. |
| **Open items can't disappear.** Every carried item is RESOLVED with evidence or CARRIED FORWARD with a next action. | Month-end issues often resolve a month later. The system tracks them until they do, and checks for the evidence itself. |
| **Every flag has a dollar amount, a source transaction, and a question.** | A flag you can't trace or act on is noise. |
| **The model reads; code does the math.** The agent transcribes each packet into an [extraction JSON](schemas/extraction.schema.json); tie-outs, bank-rec sums, duplicate checks and check aging are exact arithmetic done by [plain functions](src/pnl_analyzer/analyze.py). | Cheaper, repeatable, and a language model is never trusted with financial arithmetic. |
| **The reference implementation is a test oracle for the agent.** [`evaluate`](docs/evaluation.md) scores the agent's extractions and flags against it. | The code tests the agent on something it didn't generate, instead of testing Python against Python. |
| **Cheap phase, then expensive phase.** Intake reads only the first 3 pages; each PDF is opened once per phase. | Keeps agent token cost low. |
| **Small defenses that matter:** a script lock so runs never overlap, CSV formula-injection escaping in the manifest, and a [registry validator](tools/validate_registry.js) run before every upload. | The failure modes of unattended automation are mostly boring ones. |

---

## Sample output

Demo mode runs the full pipeline on three fictional stores with problems planted on purpose. From the generated [August 2026 report](sample-output/Reports/2026-08_portfolio_report.md):

| Store | Tier | Packet | Net sales | Prime cost (baseline) | Flags | Delivery |
|---|---|---|--:|--:|---|---|
| S01 Riverside | Active | Complete | $114,254.18 | 67.5% (64.4%) | 1 high · 4 med | 10 days (usually 9.5) |
| S02 Hilltop | Light | Complete | $89,969.33 | 66.1% (65.5%) | 1 high · 1 med | 11 days (usually 12.5) |
| S03 Lakeside | Active | **INCOMPLETE** (no BR) | $101,463.65 | 64.7% (65.3%) | 0 high · 0 med | day 15, still incomplete: **LATE** |

What it caught:

- **A vendor paid twice.** The same produce invoice on two checks two days apart. The resulting food-cost bump is attributed to the duplicate rather than flagged twice.
- **A new vendor paid by check on a Sunday,** for a round-dollar amount, and added to the vendor baseline as MONITOR.
- **A $3,600 compressor replacement,** with a question about whether it should be capitalized.
- **An overtime spike** (0.8% → 2.6% of sales) with the payroll entries behind it.
- **A bank reconciliation off by $45.00.**
- **A 111-day-old uncleared check,** escalated from an earlier month's open item.
- **Last month's open item resolved automatically** when the matching credit memo appeared in the ledger.
- **A missing bank rec,** judged late against that store's delivery rhythm and added to a drafted email to the accountant.

On the intake side: a re-sent duplicate skipped by SHA-256, a corrected P&L superseding the original (kept in `superseded/`), an unidentifiable scan quarantined with a written reason, a link-only email logged and added to the chase list, and a store graduating from the light tier.

For how the agent writes up a single store, see the [worked example](agent/templates/store_report_template.md#worked-example-s01-riverside-foods-llc-august-2026).

---

## Run the demo

Requires Python 3.10+ (and Node, only for the registry validator).

**Windows:** run the commands below in **Git Bash** (installed with [Git for Windows](https://gitforwindows.org/)) or WSL, because `scripts/run_demo.sh` is a bash script. Everything else (`pip`, `pytest`, `ruff`, `python -m pnl_analyzer.run`) works in any shell. macOS and Linux need nothing extra.

```bash
git clone https://github.com/shroach03/pnl-analyzer.git
cd pnl-analyzer
pip install -e ".[dev]"

./scripts/run_demo.sh        # generate synthetic data, run the pipeline, write sample-output/
pytest                       # unit tests per rule, edge cases, and the end-to-end planted scenarios (temp dirs only)
ruff check .                 # lint
node tools/validate_registry.js sample-data/seed/stores_registry.json
```

The report lands in `sample-output/Reports/2026-08_portfolio_report.md`, with the flags as data beside it in `2026-08_flags.json`. CI runs the same three checks (`ruff`, `pytest`, the registry validator) on every push.

## Deploy the intake script

1. Create an Apps Script project and paste in [`apps-script/Code.gs`](apps-script/Code.gs) and [`appsscript.json`](apps-script/appsscript.json) (or push them with `clasp`).
2. In **Project Settings → Script Properties**, set `INTAKE_ADDRESS` (optionally `PROCESSED_LABEL` and `ROOT_FOLDER`).
3. Run `setup()` once. It builds the Drive folder tree, the Gmail label, the manifest, and a 30-minute trigger.

---

## Production vs. demo mode

| | Production | Demo mode |
|---|---|---|
| Intake | Apps Script → Google Drive | Pre-generated Inbox in [`sample-data/inbox/`](sample-data/inbox/), same filenames and `_manifest.csv` format |
| Sweep + review | Claude agents read the PDFs into extraction JSON ([`agent/`](agent/)); code runs the checks | [`src/pnl_analyzer/`](src/pnl_analyzer/), a deterministic reference implementation that reads the PDFs itself |
| State | Registry, intake log, and baselines in a Claude Project | The same files, in a local folder |
| Write-ups | Written by the agent in its own words | Templated questions |

The reference implementation follows the same contracts as production: manifest format, provenance filenames, SHA-256 dedup, the two-field identity rule, the `_processed_*.json` format the Apps Script executes, the baseline schema, and the standing red-flag list. It covers the core of the review procedure rather than every step (it skips guest-count trends and capital-project tracking, for example). It exists so anyone can run and test the pipeline with no API key or Google account.

---

## Repository layout

```
pnl-analyzer/
├── apps-script/                  Gmail → Drive intake (Code.gs, appsscript.json)
├── agent/
│   ├── skills/                   Claude skills: process-inbox, analyze-month
│   ├── procedures/               sweep procedure + per-store review procedure
│   └── templates/                store report (with worked example) + portfolio dashboard
├── docs/
│   ├── architecture.md           components, Drive tree, data model, failure handling
│   ├── evaluation.md             scoring the agent against the reference implementation
│   └── roadmap.md                scaling from one pilot store to the full portfolio
├── schemas/baseline.schema.json  the store baseline contract
├── schemas/extraction.schema.json  the contract between the agent's reading and the code's checks
├── tools/validate_registry.js    registry checks before upload
├── config/chart_of_accounts.json
├── src/pnl_analyzer/             demo mode: intake, extract, analyze (one function per rule), report, run, evaluate
├── scripts/                      synthetic data generator + demo runner
├── sample-data/                  synthetic Inbox + seed registry, intake log, baselines
├── sample-output/                result of running the demo
├── tests/                        rule unit tests, edge cases, end-to-end, eval harness
├── pyproject.toml                packaging, pytest and ruff config
└── .github/workflows/ci.yml      ruff + pytest + registry validator
```

---

## Data and privacy

Every entity, person, vendor, bank, and dollar figure here is synthetic, produced by [`scripts/generate_sample_data.py`](scripts/generate_sample_data.py) from a fixed random seed. Real financial documents never enter the repository: [`.gitignore`](.gitignore) blocks PDFs, spreadsheets, live data folders, and credential files everywhere except the synthetic `sample-data/` and `sample-output/` folders. The intake address and other environment settings live in Apps Script's Script Properties, never in code.

## Built with

Google Apps Script · Gmail & Google Drive · Claude agent skills (multi-agent orchestration) · Python · pdfplumber · ReportLab · jsonschema · Node.js

<!--
## Results
Replace with real numbers from your own use before publishing, e.g.:
- Monthly review time cut from ~X hours to ~Y minutes across N locations
- N duplicate payments / reconciliation errors caught in the first N months
-->
