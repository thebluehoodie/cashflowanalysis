# Dashboard Deployment Guide

## Running the Dashboard

The dashboard can be run from **any directory** - both methods work identically:

### Environment Variables

Optional configuration via environment variables or `.env` file:

| Variable | Purpose | Default |
|----------|---------|---------|
| `DASH_ASSETS_VERSION` | Cache-busting version for CSS/JS assets | Git short SHA or date (YYYYMMDD) |
| `DASH_DEBUG_UI` | Enable viewport debug indicator (`1` = enabled) | `0` (disabled) |
| `DASH_HOST` | Dashboard bind address | `127.0.0.1` |
| `DASH_PORT` | Dashboard port | `8050` |

### Method 1: From repository root
```powershell
python code/dashboard_app.py
```

### Method 2: From code/ directory
```powershell
cd code
python dashboard_app.py
```

Both commands will:
- Load CSS from `code/assets/dashboard.css` automatically
- Display startup logs confirming CSS file location
- Start the dashboard at http://127.0.0.1:8050

## CSS Loading Verification

On startup, check the console output:

```
[OK] CSS file found: C:\...\code\assets\dashboard.css (3575 bytes)
[INFO] Assets folder: C:\...\code\assets
[INFO] Assets version (cache-busting): 1353849
[INFO] Current working directory: C:\...
[INFO] UI Debug mode: DISABLED
```

If CSS fails to load:
- A **red banner** will appear at the top of the dashboard: "⚠ CSS NOT LOADED - Layout degraded"
- Console will show `[ERROR]` or `[WARNING]` messages

### Cache-Busting

The dashboard automatically appends a version parameter to CSS URLs to force browser reload when assets change:

- Default: Uses git short SHA (e.g., `1353849`) or current date (e.g., `20260202`)
- Custom: Set `DASH_ASSETS_VERSION=your_version` to override
- CSS URLs become: `/assets/dashboard.css?v=1353849`

**Force CSS reload**: No need for Ctrl+Shift+R - changing `DASH_ASSETS_VERSION` or updating git commit forces reload automatically.

### Viewport Debug Mode

Enable with `DASH_DEBUG_UI=1` to show real-time viewport info:

```powershell
$env:DASH_DEBUG_UI="1"
python code/dashboard_app.py
```

A **cyan banner** will appear showing:
- Current viewport width (e.g., `1920px`)
- Active breakpoint (e.g., `DESKTOP (≥1400px): 4 KPI columns`)

This helps verify responsive layout behavior without opening DevTools.

## Browser DevTools Verification

To confirm CSS is applied correctly:

1. Open dashboard at http://127.0.0.1:8050
2. Press F12 to open DevTools
3. Select **Elements** tab
4. Find `<div class="app-shell">` in the DOM
5. Check **Computed** tab on the right:
   - `display: flex` (should be present)
   - `flex-direction: row` (on desktop)

6. Find `<div class="sidebar">`:
   - `width: 320px` (on desktop)
   - `flex: 0 0 320px`

7. Find `<div class="kpi-grid">`:
   - Desktop (≥1400px): `grid-template-columns: repeat(4, minmax(0, 1fr))`
   - Laptop (1100-1399px): `grid-template-columns: repeat(2, minmax(0, 1fr))`
   - Mobile (<1100px): `grid-template-columns: 1fr`

## Expected Layout

### Layout Policy (Confirmed)

**KPI Grid**: Uses **4 columns on large screens (≥1400px)** for maximum information density. This decision balances:
- Space efficiency: More metrics visible above the fold
- Readability: Tiles remain sufficiently sized on large monitors
- Consistency: Matches FP&A dashboard conventions

Alternative (2 columns always) was considered but rejected to optimize desktop usage.

### Desktop (≥1400px)
- Sidebar: Fixed 320px on LEFT
- Main content: Flexible width on RIGHT
- KPI tiles: 4 columns (policy confirmed)
- Charts: 2 columns

### Laptop (1100-1399px)
- Sidebar: Fixed 320px on LEFT
- KPI tiles: 2 columns
- Charts: 2 columns

### Mobile (<1100px)
- Sidebar: Stacks ABOVE (full width)
- KPI tiles: 1 column (stacked)
- Charts: 1 column (stacked)

## Net Worth / Equity Input

### Input Contract: equity_build_up_monthly.csv

Location: `outputs/equity_build_up_monthly.csv`

**Required columns**:

| Column | Type | Description |
|--------|------|-------------|
| `Loan_ID` | str | Unique loan identifier (e.g., L001) |
| `Property_ID` | str | Property identifier (e.g., P123) |
| `AsOfMonth` | str | Month-end date in YYYY-MM format |
| `Outstanding_Balance` | float | Current loan balance at month-end |
| `Previous_Balance` | float | Previous month's balance (0.0 for initial loan) |
| `Principal_Paid` | float | Principal paid during month |
| `Balance_Increase` | float | Balance increase for refinance/top-up events |
| `Loan_Event` | str | Event type: "Initial Loan", "Regular Payment", "Refinance/Top-up" |

**Validation rules**:

1. No negative `Outstanding_Balance`
2. No duplicate (Loan_ID, AsOfMonth) combinations
3. Balance continuity (for non-initial loans):
   ```
   Outstanding_Balance[t] = Previous_Balance[t] - Principal_Paid[t] + Balance_Increase[t]
   ```
4. No negative `Principal_Paid` unless `Loan_Event` contains "Refinance" or "Top-up"
5. `AsOfMonth` must be parseable as YYYY-MM

**Example**:

```csv
Loan_ID,Property_ID,AsOfMonth,Outstanding_Balance,Previous_Balance,Principal_Paid,Balance_Increase,Loan_Event
L001,P123,2024-01,500000,0.0,0.0,0.0,Initial Loan
L001,P123,2024-02,498500,500000.0,1500.0,0.0,Regular Payment
L001,P123,2024-03,497000,498500.0,1500.0,0.0,Regular Payment
```

**Validation**:

```powershell
python code/networth/loan_equity.py outputs/equity_build_up_monthly.csv
```

Output example:
```
[INFO] Loading equity data from: outputs\equity_build_up_monthly.csv
[INFO] Loaded 9 records
[INFO] Validating equity data...
[OK] Validation passed
[INFO] Computing equity summary...
[OK] Equity summary computed

AsOfMonth  Total_Outstanding_Balance  Total_Principal_Paid  Total_Balance_Increase  Net_Equity_Change  Cumulative_Equity
  2024-01                     500000                   0.0                     0.0                0.0                0.0
  2024-02                     798500                1500.0                     0.0             1500.0             1500.0
  2024-03                     795000                3500.0                     0.0             3500.0             5000.0

[OK] Saved equity summary to: outputs\equity_summary_monthly.csv
```

## Troubleshooting

### Red "CSS NOT LOADED" banner appears

**Cause**: `code/assets/dashboard.css` file missing or path incorrect

**Fix**:
1. Verify file exists: `ls code/assets/dashboard.css`
2. Check console output for `[ERROR]` messages
3. Ensure you're running Python 3.10+

### Layout still stacks vertically (CSS appears loaded)

**Cause**: Browser cached old layout or viewport too narrow

**Fix**:
1. Hard refresh: Ctrl+Shift+R (Chrome/Firefox) or Ctrl+F5
2. Clear browser cache
3. Check browser window width (need ≥1100px for side-by-side layout)
4. Check browser console (F12 → Console) for CSS parsing errors

### Assets folder not found error

**Cause**: Running from unsupported location or file moved

**Fix**:
- Always run from repository root OR `code/` directory
- Do NOT run from subdirectories like `code/analytics/`
