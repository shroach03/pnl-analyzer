# Roadmap: from pilot store to full portfolio

The pipeline was built on one pilot store and designed from the start to scale to a portfolio of roughly twenty stores served by one accountant. This is the scaling plan.

## Objective
Ingest three documents per store per month (FR, BR, GL), analyze each store against its own baseline **and** against its peers, and produce one exception-first portfolio dashboard plus a drill-down section per store, with a consolidated loose-ends register so no unexplained dollar ever drops off the radar.

## What carries over from the single-store design (unchanged)
- The per-store procedure: classify from document content, extract, cross-check FR ↔ BR ↔ GL, ratio guardrails, vendor bands, capital-project tallies, open-item carryforward.
- The baseline-memory design: one baseline file per store, same schema.
- The report tone: every flag carries a dollar amount, a GL source line, and either an explanation or an open question.

## What changes at scale

### 1. Store identity (the critical new failure mode)
Misattributing store 4's ledger to store 9 silently corrupts two baselines. The countermeasure is the canonical registry (legal entity, store code, address, bank, accountant). Every document must match its store on ≥ 2 registry fields or it goes to `Quarantine/` for human review. Nothing enters the pipeline unverified. **Status: built.**

### 2. Intake ledger and completeness
A store × document × month grid. Every run opens with received / missing / late against each store's own normal delivery lag. A chronically late packet is itself a finding. **Status: built.**

### 3. Exception-first reporting (two layers)
- **Portfolio dashboard (one page):** ranked table of all stores (sales, food %, labor %, controllable profit %, cash, flag count), the top 3–5 portfolio issues, oldest unresolved loose ends, and the missing-document list. Every red flag in the system must surface here.
- **Store sections:** the single-store format, generated for every store and read on drill-down.

**Status: consolidated report built; peer ranking planned.**

### 4. Peer benchmarking (the biggest analytical upgrade)
Portfolio median and quartiles per metric per month. Flag any store in the worst quartile on a metric for 2 consecutive months, with the dollar gap to median (for example, "store 12 food cost 31.2% vs median 28.4% ≈ $5,600/month at their volume"). Same-month, same-region peers beat any single-store trend line. A shared-vendor rate audit (cost per sales dollar by vendor across stores) exposes pricing and waste differences. **Status: planned.**

### 5. Data layer
Extracted numbers land in one master store (SQLite or a master sheet): every P&L line, vendor-month total, and cash figure, around 2,000 rows a month. Portfolio queries become instant instead of re-reading hundreds of PDFs. **Status: planned.**

### 6. Automation
An email → Drive capture rule, then a scheduled monthly pipeline: sweep the Inbox → verify / quarantine → extract → per-store analyses in parallel → portfolio comparison → dashboard and store sections → update baselines → email the dashboard. **Status: intake, orchestration and scheduled runs built; emailing the dashboard planned.**

### 7. Portfolio-only analytics
- Related-party verification: each store's management-fee formula and rent against its lease, across entities.
- Distribution pacing against consolidated cash.
- A portfolio seasonality model (many histories instead of one) for sharper "is this bill high?" judgment.
- Duplicate-payment detection across entities that share vendors.

**Status: planned.**

### 8. Consolidated loose-ends register
One register, store-tagged, sorted by age and dollars. The dashboard shows open-item count per store, so a store hoarding unresolved questions sticks out. **Status: per-store registers built; portfolio register planned.**

## Scoping questions that shaped the build
1. **One accountant with identical formats, or mixed formats?** One accountant: extraction is nearly free, and the registry carries a `format_family` field in case that changes.
2. **One operating LLC per store, or grouped entities?** Determines the registry structure and the related-party checks.
3. **How much history per store for seeding baselines?** Stores with less than 3 months start in the light tier and graduate automatically.
4. **Who reads the output?** Owner-level first. Per-store sections make district-manager distribution possible later.
