"""Batch runs behind the README tables: many orders x TWAP/VWAP/POV, plus the
market maker and pairs reports."""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from algo.core.backtester import run_backtest
from algo.core.orders import Side
from algo.runner import forecast_volume, make_exchange, run_order

ALGOS = ("twap", "vwap", "pov")

# synthetic stand-ins so everything runs without a data download
SYNTH = {"AAPL": (230, 45e6), "MSFT": (510, 20e6), "NVDA": (180, 170e6),
         "AMZN": (225, 40e6), "JPM": (300, 9e6)}


def _history(symbol: str, synthetic: bool) -> pd.DataFrame:
    if synthetic:
        from algo.data.loader import synthetic_minute

        px, vol = SYNTH.get(symbol, (100, 10e6))
        seed = sum(map(ord, symbol))
        return synthetic_minute(symbol, days=30, price=px, daily_volume=vol, seed=seed)
    from algo.data.loader import load_minute

    return load_minute(symbol)


def pick_days(history: pd.DataFrame, n_days: int, min_history: int) -> list:
    days = sorted(set(history.index.date))
    return days[min_history:][-n_days:]


def run_results(symbols, n_days=10, pct_adv=0.01, lookback=20, exchange="sim",
                synthetic=False, out_dir=Path("results"), min_history=10, seed=42) -> pd.DataFrame:
    from algo.analytics.charts import plot_compare, plot_volume_curve
    from algo.strategies import build_volume_curve

    if synthetic:
        out_dir = out_dir / "synthetic"
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    rows = []
    example = None
    for sym in symbols:
        hist = _history(sym, synthetic)
        days = pick_days(hist, n_days, min(min_history, lookback))
        if not days:
            print(f"{sym}: not enough history ({len(set(hist.index.date))} days)")
            continue
        for day in days:
            adv = forecast_volume(hist, day, lookback)
            qty = max(100, int(round(pct_adv * adv, -2)))
            side = Side.BUY if rng.random() < 0.5 else Side.SELL
            runs = {}
            for algo in ALGOS:
                runs[algo] = run_order(algo, sym, day, qty, side, hist, exchange=exchange,
                                       lookback=lookback)
                d = runs[algo].tca.to_dict()
                d.update(day=str(day), exchange=exchange)
                rows.append(d)
            example = (sym, day, runs, hist)
        print(f"{sym}: {len(days)} days, {days[0]} .. {days[-1]}")

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "results.csv", index=False)
    summary = summarize(df)
    per_symbol = df.pivot_table(index="symbol", columns="algo", values="vwap_slippage_bps",
                                aggfunc="mean").reindex(columns=list(ALGOS))
    wins = head_to_head(df)

    if example is not None:
        sym, day, runs, hist = example
        plot_compare(runs, out_dir / f"compare_{sym}_{day}.png")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            curve = build_volume_curve(hist, day, lookback=lookback)
        plot_volume_curve(curve, out_dir / f"volume_curve_{sym}.png",
                          f"{sym} volume curve used for {day} "
                          f"({len(curve.attrs['days_used'])} prior days)")

    n_orders = len(df) // len(ALGOS) if len(df) else 0
    md = [
        f"# Results ({'synthetic data' if synthetic else 'real minute bars from Yahoo'})",
        "",
        f"{n_orders} parent orders ({', '.join(symbols)}; {pct_adv:.0%} of ADV each, "
        f"random side), each run with TWAP, VWAP and POV. Exchange: {exchange}. "
        "Slippage in bps, positive = cost.",
        "",
        summary.to_markdown(floatfmt=".2f"),
        "",
        "Mean VWAP slippage by symbol (bps):",
        "",
        per_symbol.to_markdown(floatfmt=".2f"),
        "",
        wins,
    ]
    (out_dir / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("algo")
    out = pd.DataFrame({
        "orders": g.size(),
        "vs VWAP mean": g["vwap_slippage_bps"].mean(),
        "vs VWAP |mean|": g["vwap_slippage_bps"].apply(lambda s: s.abs().mean()),
        "vs VWAP std": g["vwap_slippage_bps"].std(),
        "vs arrival mean": g["arrival_slippage_bps"].mean(),
        "shortfall mean": g["shortfall_bps"].mean(),
        "tracking err %": g["tracking_error"].mean() * 100,
        "fill rate %": g["fill_rate"].mean() * 100,
    })
    out["orders"] = out["orders"].astype(str)  # otherwise the markdown table prints 50.00
    return out.reindex([a for a in ALGOS if a in out.index])


def head_to_head(df: pd.DataFrame) -> str:
    """How often VWAP ended up closer to the market VWAP than TWAP on the same order."""
    key = ["symbol", "day"]
    t = df[df.algo == "twap"].set_index(key)["vwap_slippage_bps"].abs()
    v = df[df.algo == "vwap"].set_index(key)["vwap_slippage_bps"].abs()
    both = pd.concat([t, v], axis=1, keys=["twap", "vwap"]).dropna()
    if both.empty:
        return ""
    wins = int((both.vwap < both.twap).sum())
    return (f"VWAP finished closer to the market VWAP than TWAP on {wins} of {len(both)} orders "
            f"(mean |slippage| {both.vwap.mean():.2f} vs {both.twap.mean():.2f} bps).")


def run_market_maker(symbol, history, day, exchange="sim", chart: Path | None = None, **kw):
    from algo.strategies import MarketMaker

    day = pd.Timestamp(day).date()
    bars = history[history.index.date == day]
    mm = MarketMaker(symbol, **kw)
    run_backtest({symbol: bars}, mm, make_exchange(exchange))
    rep = mm.report()
    if chart:
        from algo.analytics.charts import plot_market_maker

        plot_market_maker(rep["history"], chart,
                          f"Market maker {symbol} {day}: P&L ${rep['pnl']:,.2f}, "
                          f"fill rate {rep['fill_rate']:.1%}")
    return rep


def run_pairs(a="KO", b="PEP", synthetic=False, exchange="sim", chart: Path | None = None, **kw):
    from algo.strategies import PairsTrader

    if synthetic:
        from algo.data.loader import synthetic_pair

        da, db = synthetic_pair()
    else:
        from algo.data.loader import load_daily

        da, db = load_daily(a), load_daily(b)
    strat = PairsTrader(a, b, **kw)
    run_backtest({a: da, b: db}, strat, make_exchange(exchange))
    rep = strat.report()
    if chart:
        from algo.analytics.charts import plot_pairs

        plot_pairs(rep["history"], chart,
                   f"{a}/{b} spread: Sharpe {rep['sharpe']:.2f}, max DD {rep['max_drawdown']:.1%}",
                   strat.entry_z, strat.exit_z)
    return rep
