"""matplotlib charts for runs and results."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

BLUE, ORANGE, GREY = "#2b6cb0", "#dd6b20", "#a0aec0"


def _fills_per_bar(run) -> pd.Series:
    f = run.result.fills_frame()
    ours = pd.Series(0.0, index=run.window_bars.index)
    if len(f):
        s = f.groupby("ts")["qty"].sum()
        ours = ours.add(s, fill_value=0).reindex(ours.index).fillna(0)
    return ours


def plot_schedule(run, path: Path, title: str | None = None) -> Path:
    """Top: market volume and our fills per minute. Bottom: cumulative % done."""
    bars = run.window_bars
    ours = _fills_per_bar(run)
    t = run.tca
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.5), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    x = bars.index.tz_localize(None)
    ax1.bar(x, bars["volume"] / 1e3, width=1 / 1440, color=GREY, label="market volume")
    ax1.set_ylabel("market volume (k shares)")
    ax1b = ax1.twinx()
    ax1b.plot(x, ours / 1e3, color=BLUE, lw=1, label=f"{t.algo.upper()} fills")
    ax1b.set_ylabel("our fills (k shares)", color=BLUE)
    ax1.set_title(title or f"{t.algo.upper()} {t.side} {t.qty:,} {t.symbol}  "
                  f"(vs VWAP {t.vwap_slippage_bps:+.1f} bps, vs arrival "
                  f"{t.arrival_slippage_bps:+.1f} bps)")

    mkt_cum = bars["volume"].cumsum() / bars["volume"].sum()
    ax2.plot(x, mkt_cum * 100, color=GREY, lw=2, label="market volume")
    ax2.plot(x, ours.cumsum() / t.qty * 100, color=BLUE, lw=1.5, label=t.algo.upper())
    ax2.set_ylabel("cumulative %")
    ax2.set_ylim(0, 102)
    ax2.legend(loc="upper left", frameon=False)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    for ax in (ax1, ax2):
        ax.spines["top"].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_compare(runs: dict, path: Path) -> Path:
    """Cumulative % done for several algos on the same order, against market volume."""
    first = next(iter(runs.values()))
    bars = first.window_bars
    x = bars.index.tz_localize(None)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(x, bars["volume"].cumsum() / bars["volume"].sum() * 100, color="black", lw=2.5,
            label="market volume", alpha=0.35)
    colors = {"twap": ORANGE, "vwap": BLUE, "pov": "#38a169", "aggressive": "#e53e3e"}
    for name, run in runs.items():
        ours = _fills_per_bar(run)
        ax.plot(x, ours.cumsum() / run.tca.qty * 100, lw=1.5, color=colors.get(name),
                label=f"{name.upper()} ({run.tca.vwap_slippage_bps:+.1f} bps vs VWAP)")
    t = first.tca
    ax.set_title(f"{t.side} {t.qty:,} {t.symbol} {bars.index[0].date()}: cumulative % done")
    ax.set_ylabel("cumulative %")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.legend(frameon=False, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_volume_curve(curve: pd.Series, path: Path, title: str) -> Path:
    fig, ax = plt.subplots(figsize=(9, 3.5))
    labels = pd.date_range("2000-01-01 09:30", periods=len(curve), freq="1min")
    ax.bar(labels, curve.to_numpy() * 100, width=1 / 1440, color=BLUE)
    ax.set_ylabel("% of day's volume")
    ax.set_title(title)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_market_maker(history: pd.DataFrame, path: Path, title: str) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True)
    x = history.index.tz_localize(None) if history.index.tz is not None else history.index
    ax1.plot(x, history["pnl"], color=BLUE)
    ax1.set_ylabel("P&L ($, marked to mid)")
    ax1.set_title(title)
    ax2.step(x, history["position"], color=ORANGE, where="post")
    ax2.axhline(0, color=GREY, lw=0.8)
    ax2.set_ylabel("inventory (shares)")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def plot_pairs(history: pd.DataFrame, path: Path, title: str, entry: float, exit_: float) -> Path:
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True)
    ax1.plot(history.index, history["z"], color=BLUE, lw=1)
    for lvl, c in ((entry, "#e53e3e"), (-entry, "#e53e3e"), (exit_, GREY), (-exit_, GREY)):
        ax1.axhline(lvl, color=c, lw=0.8, ls="--")
    ax1.set_ylabel("spread z-score")
    ax1.set_title(title)
    ax2.plot(history.index, history["equity"], color=ORANGE)
    ax2.set_ylabel("P&L ($)")
    for ax in (ax1, ax2):
        ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
