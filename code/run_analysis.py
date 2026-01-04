from analytics.io import load_settings, ensure_dirs, load_ledger
from analytics.transforms import add_periods
from analytics.views import add_account_view
from analytics.aggregates import net_cashflow, intent_totals
from analytics.spikes import mom_spikes
from analytics.charts import plot_stacked
from analytics.report import save_csv, save_excel

def main():
    s = load_settings()
    ensure_dirs(s)

    df = load_ledger(s.input_csv)
    df = add_periods(df)
    df = add_account_view(df)

    operating = df[df["Account_View"] == "OPERATING"]
    capital = df[df["Account_View"] == "CAPITAL"]

    tables = {
        "Operating_YoY": intent_totals(operating, "Year"),
        "Capital_YoY": intent_totals(capital, "Year"),
        "Operating_MoM": intent_totals(operating, "Month"),
        "MoM_Spikes": mom_spikes(operating),
    }

    for name, t in tables.items():
        save_csv(t, s.tables_dir / f"{name}.csv")

    save_excel(tables, s.output_dir / "cashflow_analysis.xlsx")

    plot_stacked(
        tables["Operating_YoY"], "Year",
        s.charts_dir / "operating_yoy.png",
        "Operating Cashflow YoY"
    )

    print("Analysis complete.")

if __name__ == "__main__":
    main()
