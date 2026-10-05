# Agent layer

This folder is the production brain of the pipeline: the Claude skills that run each month, the procedures they follow, and the report formats they fill in.

| Path | What it is |
|---|---|
| [`skills/process-inbox/SKILL.md`](skills/process-inbox/SKILL.md) | **Intake sweep.** Identifies, dedupes, files, and quarantines every Inbox document, then writes the cleanup certification. |
| [`skills/analyze-month/SKILL.md`](skills/analyze-month/SKILL.md) | **Monthly orchestrator.** Runs the sweep, then fans out one review subagent per active store (light-tier stores batched 3 per subagent), reconciles baseline writes, graduates stores, and assembles one portfolio report. |
| [`procedures/sweep_procedure.md`](procedures/sweep_procedure.md) | The intake rules: disposition table, identity rule, intake-log and certification formats, completeness matrix. |
| [`procedures/monthly_analysis_procedure.md`](procedures/monthly_analysis_procedure.md) | The per-store review: cross-checks, ratio guardrails, transaction-level anomaly detection, cash, memory, and the standing red-flag list. |
| [`../schemas/extraction.schema.json`](../schemas/extraction.schema.json) | **The contract between reading and checking.** The agent transcribes each packet into this JSON; deterministic code does all the arithmetic on it. See the division of labor in the review procedure. |
| [`../docs/evaluation.md`](../docs/evaluation.md) | How to score the agent's extractions and flags against the reference implementation. |
| [`templates/store_report_template.md`](templates/store_report_template.md) | Format for each store's section, with a worked example on synthetic data. |
| [`templates/portfolio_dashboard_template.md`](templates/portfolio_dashboard_template.md) | Format for the portfolio view as the store count grows. |

## Where state lives

In production, the skills and procedures are loaded into a Claude Project along with the state files they read and write:

- `stores_registry.json`: identity rules and tiers ([example](../sample-data/seed/stores_registry.json); check it with [`tools/validate_registry.js`](../tools/validate_registry.js))
- `intake_log.json`: append-only record of every file received
- `baselines/SXX_baseline.json`: each store's memory ([example](../sample-data/seed/baselines/S01_baseline.json), [schema](../schemas/baseline.schema.json))

The documents themselves live in Google Drive, in the folder tree the Apps Script's `setupDrive()` creates.

## How a skill is used

A skill is a `SKILL.md` file: a `description` that tells Claude when to use it, plus the instructions it follows. To install these, add them as skills in Claude and upload the `procedures/` and `templates/` files to the Project the skills run in.
