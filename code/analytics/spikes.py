import pandas as pd

def mom_spikes(df, threshold=2.5, min_abs=300):
    var = df[df["Intent_L1"] == "VARIABLE_EXPENSE"]
    pivot = var.groupby(["Month", "Counterparty_Norm"])["Amount"].sum().unstack(fill_value=0)
    delta = pivot.diff()

    spikes = []
    for col in delta.columns:
        series = delta[col]
        std = series.std()
        if std == 0:
            continue
        z = (series - series.mean()) / std
        for idx, val in z.items():
            if abs(val) >= threshold and abs(delta.loc[idx, col]) >= min_abs:
                spikes.append([idx, col, delta.loc[idx, col], val])

    return pd.DataFrame(
        spikes,
        columns=["Month", "Counterparty_Norm", "MoM_Change", "Z_Score"]
    )
