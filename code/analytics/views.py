RENOVATION_KEYWORDS = [
    "RENOV", "INTERIOR", "CONTRACTOR", "CARPENTRY", "TILING",
    "PLUMBING", "ELECTRICAL", "CURTAIN", "FURNITURE"
]

PROPERTY_KEYWORDS = [
    "ONE AMBER", "TEMBUSU", "MCST", "STAMP", "BSD", "ABSD",
    "CONVEY", "LAWYER", "OTP"
]

def add_account_view(df):
    d = df["Description"].str.upper().fillna("")
    df = df.copy()

    df["Account_View"] = "OPERATING"

    df.loc[df["Intent_L1"].str.contains("TRANSFER"), "Account_View"] = "CAPITAL"
    df.loc[d.apply(lambda x: any(k in x for k in PROPERTY_KEYWORDS)), "Account_View"] = "CAPITAL"
    df.loc[d.apply(lambda x: any(k in x for k in RENOVATION_KEYWORDS)), "Account_View"] = "CAPITAL"

    return df
