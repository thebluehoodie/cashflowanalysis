import matplotlib.pyplot as plt

def plot_stacked(df, period, outpath, title):
    pivot = df.pivot_table(index=period, columns="Intent_L1", values="Amount", aggfunc="sum", fill_value=0)
    pivot.plot(kind="bar", stacked=True)
    plt.title(title)
    plt.tight_layout()
    plt.savefig(outpath)
    plt.close()
