def save_csv(df, path):
    df.to_csv(path, index=False)

def save_excel(tables, path):
    import pandas as pd
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, df in tables.items():
            df.to_excel(writer, sheet_name=name[:31], index=False)
