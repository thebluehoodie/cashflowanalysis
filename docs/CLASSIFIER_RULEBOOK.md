# Classifier Rulebook

Scope
- code/auto_classify_transactions.py

## Classification pipeline stages
1) Normalize
   - Amount coerced to numeric (NaN -> 0.0).
   - Description coerced to string.
   - Txn_ID ensured via stable hash if missing.
2) Classify
   - classify_row(desc, amount) evaluates rules in strict priority order.
3) Expand outputs
   - ClassResult fields expanded into columns.
   - Backward-compatible aliases added: Cashflow_Section, Category_L1, Category_L2, Instrument.
   - Counterparty fields derived from Description.
4) Override
   - overrides.xlsx (sheet Overrides) loaded if configured and Enabled == TRUE.
   - Only non-blank override columns apply.
5) Managerial derivation (post-override)
   - If managerial L1/L2 missing, derive from final economic fields using MANAGERIAL_DERIVE_MAP.
   - Special case: DEBT_SERVICE + CREDIT_CARD_SETTLEMENT* -> LIFESTYLE / CREDIT_CARD_SPEND_PROXY.
   - If Cashflow_Statement == TRANSFER, managerial forced to TRANSFER / INTERNAL_TRANSFER.

## Taxonomy values (as implemented)
Flow_Nature
- INCOME, EXPENSE, TRANSFER, NON-CASH

Cashflow_Statement / Cashflow_Section
- OPERATING, INVESTING, FINANCING, TRANSFER, NON-CASH

Record_Type
- TRANSACTION, SUMMARY

Asset_Context
- GENERAL, PROPERTY, CAR, FINANCIAL, UNKNOWN

Stability_Class
- STRUCTURAL_RECURRING, SEMI_RECURRING, VARIABLE, ONE_OFF

Event_Tag
- NONE, RENOVATION, PROPERTY_ACQ, TAX_EVENT

Bank_Rail
- GIRO, FAST, PAYNOW, NETS, ATM, CHEQUE, CARD, OTHER

## Cashflow_Section meanings (as implemented)
- OPERATING: salary, interest, taxes, MCST, insurance, generic inflows/outflows.
- INVESTING: property purchase downpayment, renovation.
- FINANCING: mortgage payments, car loan payments, credit card settlements.
- TRANSFER: internal transfers (self-controlled or Trust Bank OTHR Transfer).
- NON-CASH: Balance B/F summary lines and zero-amount adjustments.

UNKNOWN
- No explicit in-code narrative definitions beyond the rules above.

## Credit card settlement handling
- Rule ID: R12_CC_SETTLEMENT
- Trigger: amount < 0, issuer detected, and description contains BILL PAYMENT or CC or CARDS.
- Output:
  - Cashflow_Statement = FINANCING
  - Economic_Purpose_L1/L2 = DEBT_SERVICE / CREDIT_CARD_SETTLEMENT_<ISSUER>
  - Managerial_Purpose_L1/L2 = LIFESTYLE / CREDIT_CARD_SPEND_PROXY
  - Is_CC_Settlement = True
  - Bank_Rail = CARD
- Overrides: if final econ L1/L2 matches CREDIT_CARD_SETTLEMENT*, managerial is derived to LIFESTYLE / CREDIT_CARD_SPEND_PROXY unless explicitly overridden.

## Transfer neutralization rules
- R03_TRUST_INTERNAL: Trust Bank OTHR Transfer always classified as TRANSFER / INTERNAL_TRANSFER.
- R13_INTERNAL_TRANSFER: transfer rail or keywords AND self-entity token -> TRANSFER / INTERNAL_TRANSFER.
- Baseline_Eligible = False for transfer rules.
- apply_overrides enforces managerial Transfer when Cashflow_Statement == TRANSFER.

## Override precedence rules
- Only non-blank override cells apply (blank, NA, BLANK, or (BLANK) are ignored).
- Overrides apply column-by-column; no row is wiped.
- Baseline_Eligible overrides only if non-blank; otherwise classifier value stays.
- Override_Reason is appended to any existing reason using " | ".
- If overrides supply Managerial_Purpose_L1/L2, those are not overwritten by derivation.

## Rule IDs and priority order
Rules are evaluated top-to-bottom; first match wins.

| Priority | Rule_ID | Trigger (simplified) | Cashflow_Statement | Economic_L1/L2 | Record_Type | Baseline_Eligible |
|---:|---|---|---|---|---|---:|
| 0 | R00_BALANCE_BF | Description matches BALANCE B/F | NON-CASH | NON-CASH / BALANCE_BF | SUMMARY | False |
| 1 | R01_SALARY | Inflow and salary patterns or employer tokens | OPERATING | INCOME / SALARY | TRANSACTION | True |
| 2 | R02_INTEREST | Inflow and interest patterns | OPERATING | INCOME / INTEREST | TRANSACTION | True |
| 3 | R03_TRUST_INTERNAL | Trust Bank OTHR Transfer patterns | TRANSFER | TRANSFER / INTERNAL_TRANSFER | TRANSACTION | False |
| 4 | R04_PROPERTY_DOWNPAYMENT | Outflow and cheque or DR CO CHARGES patterns | INVESTING | HOUSING / PROPERTY_PURCHASE | TRANSACTION | False |
| 5 | R05_TAX | Outflow and IRAS/tax patterns | OPERATING | TAXES / IRAS_TAX | TRANSACTION | True |
| 6 | R06_MORTGAGE | Outflow and mortgage patterns | FINANCING | DEBT_SERVICE / MORTGAGE_PAYMENT | TRANSACTION | True |
| 7 | R07_CAR_LOAN | Outflow and car finance patterns | FINANCING | DEBT_SERVICE / CAR_LOAN_PAYMENT | TRANSACTION | True |
| 8 | R08_RENOVATION | Outflow and renovation patterns | INVESTING | HOUSING / RENOVATION | TRANSACTION | False |
| 9 | R09_MCST | Outflow and MCST patterns | OPERATING | HOUSING / HOA_CONDO_FEES | TRANSACTION | True |
| 10a | R10_INS_IN | Insurer token + inflow markers | OPERATING | INCOME / INSURANCE_PAYOUT | TRANSACTION | False |
| 10b | R11_INS_OUT | Insurer token + outflow | OPERATING | INSURANCE / PREMIUM | TRANSACTION | True |
| 11 | R12_CC_SETTLEMENT | Outflow + issuer + CC tokens | FINANCING | DEBT_SERVICE / CREDIT_CARD_SETTLEMENT_* | TRANSACTION | True |
| 12 | R13_INTERNAL_TRANSFER | Transfer/rail + self-entity | TRANSFER | TRANSFER / INTERNAL_TRANSFER | TRANSACTION | False |
| 13 | R14_OTHER_INCOME | Inflow fallback | OPERATING | INCOME / OTHER_INCOME | TRANSACTION | False |
| 14 | R15_GENERIC_OUTFLOW | Outflow fallback | OPERATING | LIFESTYLE / DISCRETIONARY | TRANSACTION | False |
| 15 | R16_ZERO_ADJ | Amount == 0 fallback | NON-CASH | NON-CASH / ACCOUNTING_ADJUSTMENT | TRANSACTION | False |

## Managerial derivation map (MANAGERIAL_DERIVE_MAP)
If (Economic_Purpose_L1, Economic_Purpose_L2) matches a key below, Managerial_Purpose is set to the mapped value. Otherwise, Managerial_Purpose defaults to the economic pair.

| Economic L1 | Economic L2 | Managerial L1 | Managerial L2 |
|---|---|---|---|
| NON-CASH | BALANCE_BF | NON-CASH | BALANCE_BF |
| INCOME | SALARY | INCOME | SALARY |
| INCOME | INTEREST | INCOME | INTEREST |
| TRANSFER | INTERNAL_TRANSFER | TRANSFER | INTERNAL_TRANSFER |
| HOUSING | PROPERTY_PURCHASE | HOUSING | PROPERTY_PURCHASE |
| TAXES | IRAS_TAX | TAXES | IRAS_TAX |
| DEBT_SERVICE | MORTGAGE_PAYMENT | DEBT_SERVICE | MORTGAGE_PAYMENT |
| DEBT_SERVICE | CAR_LOAN_PAYMENT | DEBT_SERVICE | CAR_LOAN_PAYMENT |
| HOUSING | RENOVATION | HOUSING | RENOVATION |
| HOUSING | HOA_CONDO_FEES | HOUSING | HOA_CONDO_FEES |
| INCOME | INSURANCE_PAYOUT | INCOME | INSURANCE_PAYOUT |
| INSURANCE | PREMIUM | INSURANCE | PREMIUM |
| DEBT_SERVICE | CREDIT_CARD_SETTLEMENT_CITI | LIFESTYLE | CREDIT_CARD_SPEND_PROXY |
| DEBT_SERVICE | CREDIT_CARD_SETTLEMENT_SCB | LIFESTYLE | CREDIT_CARD_SPEND_PROXY |
| DEBT_SERVICE | CREDIT_CARD_SETTLEMENT_HSBC | LIFESTYLE | CREDIT_CARD_SPEND_PROXY |
| DEBT_SERVICE | CREDIT_CARD_SETTLEMENT_UOB | LIFESTYLE | CREDIT_CARD_SPEND_PROXY |
| DEBT_SERVICE | CREDIT_CARD_SETTLEMENT_OCBC | LIFESTYLE | CREDIT_CARD_SPEND_PROXY |
| DEBT_SERVICE | CREDIT_CARD_SETTLEMENT_AMEX | LIFESTYLE | CREDIT_CARD_SPEND_PROXY |
| INCOME | OTHER_INCOME | INCOME | OTHER_INCOME |
| LIFESTYLE | DISCRETIONARY | LIFESTYLE | DISCRETIONARY |
| NON-CASH | ACCOUNTING_ADJUSTMENT | NON-CASH | ACCOUNTING_ADJUSTMENT |

UNKNOWN
- No additional rule families beyond those listed are present in code.
