def net_cashflow(df, period):
    return (
        df.groupby(period)["Amount"]
          .sum()
          .reset_index(name="Net_Cashflow")
    )

def intent_totals(df, period):
    return (
        df.groupby([period, "Intent_L1"])["Amount"]
          .sum()
          .reset_index()
    )
