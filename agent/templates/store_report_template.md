# [S0X · Entity Name] Monthly Financial Overview: [MONTH YEAR]

*Documents received: FR ✓ / BR ✓ / GL ✓. Reconciliation check: [Unreconciled amount = $0.00 ✓/✗]*

## Headline

Net sales $[X] ([+/−]% vs prior month, [+/−]% vs same month last year) on [X] guest checks at $[X] average check. Net income $[X]. Ending book cash $[X] with $[X] of open checks outstanding. [One sentence: the single most important thing the owner should know this month.]

## Flags: each with a dollar amount and a disposition

For every item: **what it is → source (GL date/payee/amount) → dollar impact → EXPLAINED or OPEN QUESTION.**

1. …
2. …

## Ratio check vs baseline

Food cost [X]% (norm [lo–hi]) · Packaging [X]% · Crew labor [X]% · Total labor [X]% · Controllables [X]% · Utilities $[X] · Management fee per rule? [✓/✗] · Royalty/brand fund in contractual range? [✓/✗]

Only breaches get narrative above. In-range items are listed here as proof they were checked.

## Vendor watch

New payees this month: [list with amounts, or "none"]. Vendors outside their band: [list, or "none"]. Missing expected recurring charges: [list, or "none"]. Fixed-amount drift: [list, or "none"].

## Capital projects

[Running totals from the baseline; new draws this month; on-budget question if applicable.]

## Loose ends (nothing leaves this list without a resolution)

| ID | Item | Status | Next action |
|---|---|---|---|
| OI-… | … | OPEN / MONITOR / RESOLVED (evidence) | … |

## Trend read

[2–4 sentences: trajectory of sales, margins, cash; anything building toward a problem; what to watch next month.]

---
---

# WORKED EXAMPLE: S01 (Riverside Foods LLC), August 2026

*All figures are synthetic and match [the demo's sample report](../../sample-output/Reports/2026-08_portfolio_report.md). Documents received: FR ✓ / BR ✓ / GL ✓. Unreconciled amount = $0.00 ✓*

## Headline

Net sales $114,254.18 (−3.6% vs July, +1.5% vs the 6-month average). Operating income $7,983.38, **7.0% of sales, about half the store's usual ~13.4%**. Ending book cash $50,630.15 with $997.40 of open checks. The most important thing this month: the margin drop is concentrated in items 1, 2 and 4 below (about $7,900 of excess cost), and item 1, a double-paid invoice, is recoverable.

## Flags

1. **Possible double payment, Prairie Produce Co.** Invoice INV-44817 paid twice: 8/5 check #10429 and 8/7 check #10443, **$1,284.50** each. This also explains the food cost bump (27.3% vs a 26.0% norm; excluding the duplicate, food cost is in range). **OPEN QUESTION:** confirm with the vendor and request a refund or credit.
2. **Repairs & maintenance $6,035.30** vs a ~1.3%-of-sales norm, **~$4,560 above expected.** Coldline Refrigeration Svc 8/19 check #10444, $3,600.00, "compressor replacement, walk-in cooler." **OPEN QUESTION:** a compressor extends the equipment's life; should this be capitalized rather than expensed?
3. **New vendor paid on a Sunday.** QuickFix Handyman LLC, 8/16 check #10445, **$950.00**, round amount, memo "misc repairs." **OPEN QUESTION:** what was done, who approved it, and is it recurring? Added to the baseline as MONITOR.
4. **Overtime 2.6% of sales** vs a 0.8% norm, **~$2,050 above expected**, split across both payroll runs (8/14 $1,312.92, 8/28 $1,657.69). **OPEN QUESTION:** open positions, training, or scheduling? Request the overtime report by employee.
5. **Stale check #10211,** Fern Valley Waste Services, **$612.40**, dated 5/12, now 111 days outstanding. Carried from `OI-2026-05-A` and escalated. **OPEN QUESTION:** did the vendor receive it? Void and reissue if lost.
6. **Insurance $0.00** vs $1,070 normal. **EXPLAINED:** credit memo CM-2291 (8/12, JE-2291) reversed July's duplicate premium, which resolves `OI-2026-07-A`.

## Ratio check vs baseline

Food cost 27.3% (explained by item 1) · Beverage 4.5% ✓ · Packaging 3.4% ✓ · Crew labor 19.8% ✓ · Overtime 2.6% ✗ (item 4) · Payroll taxes 4.3% ✓ · Repairs 5.3% ✗ (item 2) · Utilities 3.5% ✓ (summer uplift applied) · Supplies 1.2% ✓ · Marketing 1.0% ✓ · Card fees 2.4% ✓ · Royalty & brand fund 5.0% ✓ · Rent, property tax, management salaries fixed ✓

## Vendor watch

New payees: QuickFix Handyman LLC $950.00 (item 3). Outside band: Coldline Refrigeration Svc (item 2). Missing recurring: none. Fixed-amount drift: none.

## Capital projects

None open. Pending the answer to item 2, the compressor may become the first entry.

## Loose ends

| ID | Item | Status | Next action |
|---|---|---|---|
| OI-2026-07-A | July insurance premium posted twice ($1,070.00) | RESOLVED (credit memo CM-2291, 8/12) | None |
| OI-2026-05-A | Check #10211, Fern Valley Waste Services, $612.40, outstanding since 5/12 | OPEN (escalated) | Confirm receipt; void and reissue |
| OI-2026-08-A | Prairie Produce Co INV-44817 paid twice, $1,284.50 | OPEN | Request refund or credit |
| OI-2026-08-B | Compressor replacement $3,600.00 | OPEN | Decide expense vs capitalize |
| OI-2026-08-C | Overtime ~$2,050 above norm | MONITOR | Overtime report by employee |
| OI-2026-08-D | QuickFix Handyman LLC $950.00, new vendor, Sunday check | OPEN | Invoice and approver |

## Trend read

Sales are holding at the summer run rate, and every ratio outside repairs, overtime, and food cost is in line. The margin drop is concentrated in a handful of transactions rather than a drift in operations, which is the better kind of bad month. Next month: confirm the Prairie Produce credit arrives, and watch whether overtime returns to its 0.8% norm.
