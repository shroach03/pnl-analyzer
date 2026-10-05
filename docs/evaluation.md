# Evaluating the agent

The reference implementation in [`src/pnl_analyzer/`](../src/pnl_analyzer/) is deterministic code. That makes it a **test oracle** for the production agent: run the real agent on the same synthetic inbox and score what it produced against what the code produced.

Testing the Python against data the Python generated would be circular, because it only shows the code agrees with itself. The eval below tests something the code can't: whether a language model *reads* the accountant PDFs correctly and reaches the same findings.

## What is being scored

The agent's job is split in two (see [the review procedure](../agent/procedures/monthly_analysis_procedure.md)):

| Stage | Who | Scored by |
|---|---|---|
| **Read**: transcribe P&L lines, GL rows and bank-rec figures from the PDFs into JSON | Agent | `evaluate extractions`: field-by-field diff against the reference reader's output |
| **Check**: tie-outs, bank-rec sums, duplicates, aging, variance | Code (same code for both) | Not scored: it is the oracle |
| **Flag**: which exceptions the month contains | Agent end to end, or agent extraction fed to the code | `evaluate flags`: recall and precision against the reference flags |

Financial arithmetic is deliberately kept out of the model, so an extraction error is the main way the agent can be wrong. That is what the extraction score isolates.

## Procedure

1. Generate the synthetic inbox and the reference output. The reference dumps its own reading of each packet and its flags:

   ```bash
   python scripts/generate_sample_data.py --out /tmp/eval
   cp -r /tmp/eval/seed /tmp/eval/ws
   python -m pnl_analyzer.run --inbox /tmp/eval/inbox --workspace /tmp/eval/ws \
       --month 2026-08 --as-of 2026-09-15T09:00 --dump-extracted /tmp/eval/reference_extracted
   # reference flags are in /tmp/eval/ws/Reports/2026-08_flags.json
   ```

2. Give the agent `/tmp/eval/inbox` and the sweep and review procedures. Have it write one extraction JSON per store, `{store_id}_{month}.json`, matching [`schemas/extraction.schema.json`](../schemas/extraction.schema.json), into `/tmp/eval/agent_extracted`.

3. Score the reading:

   ```bash
   python -m pnl_analyzer.evaluate extractions /tmp/eval/reference_extracted /tmp/eval/agent_extracted
   ```

   Each store prints `exact match` or one line per difference (`FR 5010: expected 31219.1, got 31291.1`, `GL row missing: ...`, `BR ... outstanding_checks missing: ...`).

4. Score the findings by running the code on the agent's extractions, from a fresh copy of the seed state, and comparing flags:

   ```bash
   cp -r /tmp/eval/seed /tmp/eval/ws_agent
   python -m pnl_analyzer.run --inbox /tmp/eval/inbox --workspace /tmp/eval/ws_agent \
       --month 2026-08 --as-of 2026-09-15T09:00 --extracted-dir /tmp/eval/agent_extracted
   python -m pnl_analyzer.evaluate flags /tmp/eval/ws/Reports/2026-08_flags.json /tmp/eval/ws_agent/Reports/2026-08_flags.json
   ```

   To score flags the agent wrote itself, put them in the same shape as `flags.json` (a list of `{store_id, severity, category, account, amount, title}`) and pass that file as the candidate.

## How matching works

Flags are matched one-to-one on `(store_id, category, account, |amount|)`, with a one-cent tolerance. Within each (store, category, account) group the amounts are sorted and paired, which gives the largest possible number of matches whatever order the flags arrive in (matching each reference flag to the first candidate that fits can lose a match to an earlier pair). Wording is ignored because an agent phrases findings its own way, and so is sign, because "$45 over" and "-$45" are the same finding. Severity is compared afterwards and reported separately. The output lists:

- **MISSED**: a reference flag the agent did not raise (lowers recall)
- **EXTRA**: a flag the reference did not raise (lowers precision)
- **SEVERITY**: matched flags the agent rated differently

`--min-recall` and `--min-precision` (both default 1.0) set the pass bar, and the command exits non-zero below it, so the same command can gate a CI job that runs the agent on a schedule.

## What is tested without an agent

The scoring code and the extraction seam are covered by [`tests/test_evaluate.py`](../tests/test_evaluate.py), which runs in CI with no API access. It checks that the reference's own extraction files, fed back in, give identical flags and baselines, and that a single misread figure produces a flag difference the scorer reports. Running the agent itself needs credentials and a model call, so it is a manual or scheduled step, not part of `pytest`.

## Limits

- The synthetic PDFs are cleaner than real accountant output. A perfect score here means the procedure works on well-formed documents, not on every layout the accountants send. Add scanned, rotated and multi-page-table variants to the generator to raise the bar.
- The planted scenarios are a fixed list. An agent can score 100% by getting those right and still miss a class of problem nobody planted.
- Only the checks the code implements are scored. Vendor bands, management-fee rules and capital-project tracking are not in the reference yet.
