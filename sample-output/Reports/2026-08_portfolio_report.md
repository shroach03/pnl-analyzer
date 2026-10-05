# Portfolio Review: August 2026

*Example Restaurant Group (fictional) · generated 2026-09-15 09:00 · synthetic demo data*

**3 stores reviewed · 2 packets complete · 1 incomplete · 7 flags (2 high) · 1 tier graduation**

## Needs attention

- **S03 Lakeside**: packet INCOMPLETE, missing BR; delivery at day 15 and still incomplete (usually 9): **LATE**. Month not closed; rerun when it arrives.
- **S01 Riverside**: Possible duplicate payment: Prairie Produce Co INV-44817 ($1,284.50).
- **S02 Hilltop**: Bank reconciliation does not balance ($45.00).
- Inbox: `20260912_f3f71406_IMG_0912_scan.pdf` quarantined: 1 identity field(s) matched (S02: address_fragment); document type not recognized; reporting period not found
- Inbox: `LINK_ONLY_20260913_dae9c274.txt` link only, logged and chased: Email had no PDF attachment; the body links to documents, so a person must download them.

## Dashboard

| Store | Tier | Packet | Net sales | vs trailing avg | Prime cost (baseline) | Op. margin | Flags | Delivery |
|---|---|---|--:|--:|--:|--:|---|---|
| S01 Riverside | Active | Complete | $114,254.18 | +1.5% | 67.5% (64.4%) | 7.0% | 1 high · 4 med | 10 days (usually 9.5) |
| S02 Hilltop | Light | Complete | $89,969.33 | +1.3% | 66.1% (65.5%) | 9.3% | 1 high · 1 med | 11 days (usually 12.5) |
| S03 Lakeside | Active | **INCOMPLETE** (no BR) | $101,463.65 | +1.5% | 64.7% (65.3%) | 12.9% | 0 high · 0 med | day 15 and still incomplete (usually 9): **LATE** |

*Prime cost = food, beverage and paper cost plus all labor, as a percent of net sales.*

## Intake sweep

| Received | Inbox file | Disposition | Filed as / reason |
|---|---|---|---|
| 2026-09-08 16:02 | `20260908_c1e15fbd_Riverside_Aug2026_Financials.pdf` | Filed | `S01_FR_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-08 16:02 | `20260908_c1e15fbd_GL_Detail_Aug2026.pdf` | Filed | `S01_GL_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-09 11:20 | `20260909_fbc5b4bd_Lakeside_P&L_2026-08.pdf` | Filed | `S03_FR_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-09 11:20 | `20260909_fbc5b4bd_Lakeside_GL_2026-08.pdf` | Filed | `S03_GL_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-10 10:15 | `20260910_6d743faa_BankRec_Aug.pdf` | Filed | `S01_BR_2026-08.pdf` (matched on legal_entity, store_code, address_fragment, bank_accounts) |
| 2026-09-10 10:15 | `20260910_6d743faa_GL_Detail_Aug2026.pdf` | Duplicate, skipped | Identical to `S01_GL_2026-08.pdf` |
| 2026-09-11 09:30 | `20260911_c7feb29a_Hilltop P&L Aug 2026.pdf` | Filed | `S02_FR_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-11 09:30 | `20260911_c7feb29a_Hilltop GL Aug 2026.pdf` | Filed | `S02_GL_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-11 09:30 | `20260911_c7feb29a_Hilltop Bank Rec Aug 2026.pdf` | Filed | `S02_BR_2026-08.pdf` (matched on legal_entity, store_code, address_fragment, bank_accounts) |
| 2026-09-12 08:05 | `20260912_f3f71406_IMG_0912_scan.pdf` | Quarantined | 1 identity field(s) matched (S02: address_fragment); document type not recognized; reporting period not found |
| 2026-09-11 09:30 | (archive) `S02_FR_2026-08.pdf` | Earlier copy moved to superseded/ | `Stores/S02/2026-08/superseded/S02_FR_2026-08_superseded_20260915.pdf` |
| 2026-09-12 14:45 | `20260912_3bdb791c_Hilltop P&L Aug 2026 REVISED.pdf` | Filed, supersedes earlier copy | `S02_FR_2026-08.pdf` (matched on legal_entity, store_code, address_fragment) |
| 2026-09-13 07:40 | `LINK_ONLY_20260913_dae9c274.txt` | Link only, logged and chased | Email had no PDF attachment; the body links to documents, so a person must download them. |

Cleanup certification written: `_processed_20260915_0900.json`.

## S01 Riverside: full review

Net sales $114,254.18 (+1.5% vs 6-month average) · COGS 35.2% · labor 32.3% · prime cost 67.5% · operating income $7,983.38 (7.0%)

### Flags

1. **[HIGH] Possible duplicate payment: Prairie Produce Co INV-44817: $1,284.50**
   - Source: GL 2026-08-05 CHK10429 | Prairie Produce Co | INV-44817 | $1,284.50; GL 2026-08-07 CHK10443 | Prairie Produce Co | INV-44817 | $1,284.50
   - Open question: Confirm with Prairie Produce Co whether INV-44817 was paid twice and request a refund or credit.
   - Tracked as `OI-2026-08-A`
2. **[MEDIUM] Repairs & Maintenance above baseline: $4,559.07**
   - What moved: 5.3% of sales vs 1.3% expected (+4.0 pts)
   - Source: GL 2026-08-19 CHK10444 | Coldline Refrigeration Svc | Compressor replacement - walk-in cooler | $3,600.00; GL 2026-08-16 CHK10445 | QuickFix Handyman LLC | Misc repairs | $950.00; GL 2026-08-12 CHK10435 | Coldline Refrigeration Svc | INV-55239 | $765.41
   - Open question: Is this a one-time repair? If it extends the equipment's life, it may belong on the balance sheet rather than in expense.
   - Tracked as `OI-2026-08-B`
3. **[MEDIUM] Overtime above baseline: $2,052.47**
   - What moved: 2.6% of sales vs 0.8% expected (+1.8 pts)
   - Source: GL 2026-08-28 PR-0828 | Payroll Batch | Overtime premium | $1,657.69; GL 2026-08-14 PR-0814 | Payroll Batch | Overtime premium | $1,312.92
   - Open question: What drove it: open positions, training, or scheduling? Request the overtime report by employee.
   - Tracked as `OI-2026-08-C`
4. **[MEDIUM] Unusual payment to QuickFix Handyman LLC: vendor not seen in prior months; check dated on a Sunday: $950.00**
   - Source: GL 2026-08-16 CHK10445 | QuickFix Handyman LLC | Misc repairs | $950.00
   - Open question: What is this, who approved it, and is it recurring? Request the invoice.
   - Tracked as `OI-2026-08-D`
5. **[MEDIUM] Outstanding check #10211 is 111 days old (carried item OI-2026-05-A, escalated): $612.40**
   - Source: BR outstanding checks | #10211 2026-05-12 Fern Valley Waste Services
   - Open question: Confirm the payee received it; void and reissue if lost.

### P&L against baseline

| Account | Actual | % of sales | Expected | Change | $ impact | Status |
|---|--:|--:|--:|--:|--:|---|
| 5010 Food Cost | $31,219.10 | 27.3% | 26.0% | +1.3 pts | +$1,522.92 | explained |
| 5020 Beverage Cost | $5,141.44 | 4.5% | 4.5% | 0.0 pts | +$37.34 | ok |
| 5030 Paper & Packaging | $3,884.64 | 3.4% | 3.4% | 0.0 pts | +$29.79 | ok |
| 6010 Crew Wages | $22,622.33 | 19.8% | 19.8% | 0.0 pts | -$40.32 | ok |
| 6015 Overtime | $2,970.61 | 2.6% | 0.8% | +1.8 pts | +$2,052.47 | **FLAG** |
| 6020 Management Salaries | $6,400.00 | 5.6% | $6,400.00 | fixed | $0.00 | ok |
| 6030 Payroll Taxes & Benefits | $4,912.93 | 4.3% | 4.3% | 0.0 pts | +$23.62 | ok |
| 6110 Repairs & Maintenance | $6,035.30 | 5.3% | 1.3% | +4.0 pts | +$4,559.07 | **FLAG** |
| 6120 Utilities | $3,976.05 | 3.5% | 3.5% | 0.0 pts | -$13.82 | ok |
| 6130 Operating Supplies | $1,371.05 | 1.2% | 1.2% | 0.0 pts | -$4.31 | ok |
| 6140 Local Marketing | $1,142.54 | 1.0% | 1.0% | 0.0 pts | +$1.85 | ok |
| 6150 Card Processing Fees | $2,742.10 | 2.4% | 2.4% | 0.0 pts | -$6.50 | ok |
| 6210 Rent | $7,500.00 | 6.6% | $7,500.00 | fixed | $0.00 | ok |
| 6220 Insurance | $0.00 | 0.0% | $1,070.00 | fixed | -$1,070.00 | explained |
| 6230 Property Tax | $640.00 | 0.6% | $640.00 | fixed | $0.00 | ok |
| 6240 Royalty & Brand Fund | $5,712.71 | 5.0% | 5.0% | 0.0 pts | $0.00 | ok |

- 5010 Food Cost: Movement is explained by the suspected duplicate payment INV-44817 ($1,284.50), flagged separately; excluding it, the line is within its baseline range.
- 6220 Insurance: Movement is explained by resolved open item OI-2026-07-A; excluding it, the line is within its baseline range.

### Bank reconciliation

CASH IN BANK - FIRST EXAMPLE BANK: statement $48,215.37 + deposits in transit $3,412.18 − outstanding checks $997.40 = $50,630.15 vs books $50,630.15. **Balanced.**

### Loose ends

| ID | Item | Status | Next action / evidence |
|---|---|---|---|
| `OI-2026-07-A` | July insurance premium appears to have been posted twice (Summit Mutual Insurance, $1,070.00). | **RESOLVED** | GL 2026-08-12 JE-2291 \| Summit Mutual Insurance \| Credit memo CM-2291 - duplicate July premium \| -$1,070.00 |
| `OI-2026-05-A` | Check #10211 to Fern Valley Waste Services ($612.40, dated 2026-05-12) has not cleared the bank. | CARRIED FORWARD (opened 2026-05) | Still open at month-end. Confirm the vendor received payment; void and reissue if lost. |
| `OI-2026-08-A` | Possible duplicate payment: Prairie Produce Co INV-44817 ($1,284.50). | NEW | Confirm with Prairie Produce Co whether INV-44817 was paid twice and request a refund or credit. |
| `OI-2026-08-B` | Repairs & Maintenance above baseline ($4,559.07). | NEW | Is this a one-time repair? If it extends the equipment's life, it may belong on the balance sheet rather than in expense. |
| `OI-2026-08-C` | Overtime above baseline ($2,052.47). | NEW | What drove it: open positions, training, or scheduling? Request the overtime report by employee. |
| `OI-2026-08-D` | Unusual payment to QuickFix Handyman LLC: vendor not seen in prior months; check dated on a Sunday ($950.00). | NEW | What is this, who approved it, and is it recurring? Request the invoice. |

### Notes

- P&L ties to the general ledger on every account.
- Seasonality applied to 6120 Utilities: expected 3.49% of sales (off-season mean x 1.20); actual 3.48%; within range, not flagged.

## S02 Hilltop: light review (notable items only)

Net sales $89,969.33 (+1.3% vs 2-month average) · COGS 34.4% · labor 31.7% · prime cost 66.1% · operating income $8,361.32 (9.3%)

### Notable items

1. **[HIGH] Bank reconciliation does not balance: $45.00**
   - Source: BR CASH IN BANK - SECOND SAMPLE BANK: adjusted bank $34,180.31 vs books $34,135.31
   - Open question: Ask the accountant for the reconciling item behind the difference.
   - Tracked as `OI-2026-08-A`
2. **[MEDIUM] Local Marketing above baseline: $1,502.57**
   - What moved: 2.7% of sales vs 1.0% expected (+1.7 pts)
   - Source: GL 2026-08-21 CHK20332 | Hometown Print & Signs | Grand re-opening banners | $1,500.00; GL 2026-08-28 CHK20330 | Hometown Print & Signs | INV-32339 | $900.81
   - Open question: Confirm the spend was approved and whether it is a one-time campaign or a new run rate.
   - Tracked as `OI-2026-08-B`

### Bank reconciliation

CASH IN BANK - SECOND SAMPLE BANK: statement $31,904.66 + deposits in transit $2,688.40 − outstanding checks $412.75 = $34,180.31 vs books $34,135.31. **Off by $45.00.**

### Loose ends

| ID | Item | Status | Next action / evidence |
|---|---|---|---|
| `OI-2026-08-A` | Bank reconciliation does not balance ($45.00). | NEW | Ask the accountant for the reconciling item behind the difference. |
| `OI-2026-08-B` | Local Marketing above baseline ($1,502.57). | NEW | Confirm the spend was approved and whether it is a one-time campaign or a new run rate. |

### Notes

- P&L ties to the general ledger on every account.
- Superseded P&L `S02_FR_2026-08_superseded_20260915.pdf` did not tie (6130 off by $100.00); `S02_FR_2026-08.pdf` is used and ties.
- Seasonality applied to 6120 Utilities: expected 3.49% of sales (mean of prior in-season months); actual 3.50%; within range, not flagged.

## S03 Lakeside: full review · INCOMPLETE

> **INCOMPLETE.** Missing BR. Reviewed what arrived (`S03_FR_2026-08.pdf`, `S03_GL_2026-08.pdf`); bank-dependent checks and items could not be verified. The month stays open.

Net sales $101,463.65 (+1.5% vs 4-month average) · COGS 33.6% · labor 31.1% · prime cost 64.7% · operating income $13,095.62 (12.9%)

### Flags

No flags. Every line is within its baseline range and the standing red-flag checks came back clean on the documents received.

### P&L against baseline

| Account | Actual | % of sales | Expected | Change | $ impact | Status |
|---|--:|--:|--:|--:|--:|---|
| 5010 Food Cost | $26,115.35 | 25.7% | 26.2% | -0.5 pts | -$471.74 | ok |
| 5020 Beverage Cost | $4,521.36 | 4.5% | 4.5% | -0.1 pts | -$90.49 | ok |
| 5030 Paper & Packaging | $3,488.69 | 3.4% | 3.4% | 0.0 pts | +$25.06 | ok |
| 6010 Crew Wages | $20,226.43 | 19.9% | 20.0% | -0.1 pts | -$60.63 | ok |
| 6015 Overtime | $815.20 | 0.8% | 0.8% | 0.0 pts | -$5.67 | ok |
| 6020 Management Salaries | $6,100.00 | 6.0% | $6,100.00 | fixed | $0.00 | ok |
| 6030 Payroll Taxes & Benefits | $4,393.29 | 4.3% | 4.3% | +0.1 pts | +$59.22 | ok |
| 6110 Repairs & Maintenance | $1,305.74 | 1.3% | 1.3% | 0.0 pts | -$0.35 | ok |
| 6120 Utilities | $3,210.51 | 3.2% | 3.5% | -0.3 pts | -$339.27 | ok |
| 6130 Operating Supplies | $1,217.31 | 1.2% | 1.2% | 0.0 pts | +$6.40 | ok |
| 6140 Local Marketing | $1,003.40 | 1.0% | 1.0% | 0.0 pts | -$0.94 | ok |
| 6150 Card Processing Fees | $2,387.57 | 2.4% | 2.4% | -0.1 pts | -$50.91 | ok |
| 6210 Rent | $6,900.00 | 6.8% | $6,900.00 | fixed | $0.00 | ok |
| 6220 Insurance | $1,020.00 | 1.0% | $1,020.00 | fixed | $0.00 | ok |
| 6230 Property Tax | $590.00 | 0.6% | $590.00 | fixed | $0.00 | ok |
| 6240 Royalty & Brand Fund | $5,073.18 | 5.0% | 5.0% | 0.0 pts | $0.00 | ok |

### Loose ends

| ID | Item | Status | Next action / evidence |
|---|---|---|---|
| `OI-2026-07-A` | Utility deposit refund of $300.00 from Metro Water Utility expected after meter replacement. | **RESOLVED** | GL 2026-08-18 JE-3188 \| Metro Water Utility \| Deposit refund - meter replacement \| -$300.00 |
| `OI-2026-08-A` | 2026-08 packet incomplete: missing BR. | NEW | Chase the accountant; rerun once the document arrives. |

### Notes

- P&L ties to the general ledger on every account.
- Seasonality applied to 6120 Utilities: expected 3.50% of sales (mean of prior in-season months); actual 3.16%; within range, not flagged.

## Tier changes

- **S02 Hilltop** reached 3 months of history and graduates from light to active. Next month gets a full review.

## Baseline writes

| Store | Rollback point | Written | Verified on re-read | Month closed |
|---|---|---|---|---|
| S01 | `rollback/2026-08_pre-run/S01_baseline.json` | yes | yes | yes |
| S02 | `rollback/2026-08_pre-run/S02_baseline.json` | yes | yes | yes |
| S03 | `rollback/2026-08_pre-run/S03_baseline.json` | yes | yes | no (packet incomplete) |

## Draft email to the accountant

```text
Subject: August 2026 close: a few open items

Hi team,

Thanks for the August 2026 packages. A few things we still need:

- Riverside: outstanding check #10211 is 111 days old ($612.40). Can you confirm whether it should be voided and reissued?
- Hilltop: bank reconciliation does not balance by $45.00. Could you send the reconciling item?
- Lakeside: the August 2026 bank reconciliation has not arrived (we received a portal link only; please send the PDF).

Thank you,
[Your name]
```
