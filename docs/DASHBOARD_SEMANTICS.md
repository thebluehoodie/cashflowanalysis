# Dashboard Semantics

Scope
- code/dashboard_app.py

## Input contract and validation
- Required fields (hard failures if missing):
  - Amount
  - Cashflow_Section or Cashflow_Statement
  - Date or YearMonth
- Hard failure if Cashflow_Section has unrecognized values (valid: OPERATING, INVESTING, FINANCING, TRANSFER, NON-CASH).
- Booleans are coerced for Is_CC_Settlement and Baseline_Eligible; invalid values raise ValueError.

## Landing page KPI definitions
All KPIs are computed from the filtered dataset and restricted to cash movement sections only: OPERATING, INVESTING, FINANCING.
- Net Cash Movement = sum(Amount) for OPERATING + INVESTING + FINANCING
- Operating Cash = sum(Amount) where Cashflow_Section == OPERATING
- Investing Cash = sum(Amount) where Cashflow_Section == INVESTING
- Financing Cash = sum(Amount) where Cashflow_Section == FINANCING

Notes
- Sign is preserved (negative outflows, positive inflows).
- KPIs use the current Cash Lens setting (see below).

## Filter semantics and exclusions
Filters are applied in this order (per code flow):
1) YearMonth range filter (inclusive)
2) Section filter (Cashflow_Section)
3) Exclude Transfers (default on)
4) Exclude Summary (default on)
5) Category filters (Category_L1, Category_L2)
6) Baseline mode filter (Baseline_Eligible == True)
7) Include NON-CASH (default off)
8) Search filter (Description and Counterparty_Core contains search text)

Exclusion logic details
- Summary rows are those where Record_Type == SUMMARY OR Category_L2 == BALANCE_BF.
- Transfer rows are those where Cashflow_Section == TRANSFER.
- Non-cash rows are those where Cashflow_Section == NON-CASH (excluded unless explicitly included).

## Net economic vs gross movement lens
Cash Lens affects all KPI and time-series calculations:
- NET_ECONOMIC (default): transfers are removed from monthly_source before KPI and chart aggregation.
- GROSS_MOVEMENT: transfers remain included in monthly_source.

Note
- If "Exclude Transfers" is checked, transfers are already removed; in that case NET_ECONOMIC and GROSS_MOVEMENT yield the same dataset.

## Spend lens semantics
Spend lens affects only the Operating Spend chart:
- DIRECT (default): operating outflows exclude Is_CC_Settlement == True.
- INCLUDE_CC_PROXY: operating outflows include Is_CC_Settlement == True by adding CC settlement rows back into the spend base.

## Net economic vs gross movement lens output usage
- Waterfall chart uses the same filtered dataset as KPIs.
- Drivers chart uses the same filtered dataset, then aggregates by Category_L2.
- Net cashflow line chart uses monthly KPI aggregation.

## Reconciliation assertions expected
- UNKNOWN: dashboard_app.py does not perform reconciliation checks.
- The only reconciliation logic in scope is in clean_bank_statement.py (reconciliation_report.csv), not enforced in the dashboard.
