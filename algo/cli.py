"""Command line entry point: `algo <command>`.

  algo fetch AAPL MSFT ...          cache minute bars (and --daily for daily bars)
  algo run vwap --symbol AAPL --qty 200000 --date 2026-09-25
  algo compare --symbol AAPL --qty 200000 --date 2026-09-25
  algo results                      5 symbols x N days x 3 algos -> results/
  algo mm --symbol AAPL --date 2026-09-25
  algo pairs KO PEP
  algo bench                        bars/sec with the Python and C++ exchanges
"""

from __future__ import annotations

import argparse
import sys
from datetime import time
from pathlib import Path

from algo.core.orders import Side

DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM"]
PAIR_SYMBOLS = ["KO", "PEP"]


def _time(s: str | None) -> time | None:
    if not s:
        return None
    h, m = s.split(":")
    return time(int(h), int(m))


def _history(args):
    if getattr(args, "synthetic", False):
        from algo.data.loader import synthetic_minute

        return synthetic_minute(args.symbol, seed=args.seed)
    from algo.data.loader import load_minute

    return load_minute(args.symbol)


def _default_day(history) -> str:
    return str(sorted(set(history.index.date))[-1])


def cmd_fetch(args) -> None:
    from algo.data.loader import fetch_daily, fetch_minute

    symbols = args.symbols or DEFAULT_SYMBOLS + PAIR_SYMBOLS
    if args.daily:
        fetch_daily(symbols)
    else:
        fetch_minute(symbols)
        fetch_daily(PAIR_SYMBOLS if not args.symbols else [])


def _run_one(args, algo: str):
    from algo.runner import run_order

    history = _history(args)
    day = args.date or _default_day(history)
    return run_order(algo, args.symbol, day, args.qty, Side[args.side.upper()], history,
                     start=_time(args.start), end=_time(args.end), exchange=args.exchange,
                     lookback=args.lookback), day


def cmd_run(args) -> None:
    run, day = _run_one(args, args.algo)
    print(f"{day}  exchange={args.exchange}\n")
    print(run.tca.pretty())
    if args.chart:
        from algo.analytics.charts import plot_schedule

        path = Path(args.chart)
        plot_schedule(run, path)
        print(f"\nchart -> {path}")


def cmd_compare(args) -> None:
    rows = []
    for algo in ("twap", "vwap", "pov"):
        run, day = _run_one(args, algo)
        rows.append(run.tca)
        print(run.tca.pretty(), "\n")
    print(f"{'algo':<6}{'vs arrival':>12}{'vs VWAP':>10}{'filled':>9}{'tracking':>10}")
    for t in rows:
        print(f"{t.algo:<6}{t.arrival_slippage_bps:>+11.2f} {t.vwap_slippage_bps:>+9.2f} "
              f"{t.fill_rate:>8.1%} {t.tracking_error:>9.2%}")
    best = min(rows, key=lambda t: abs(t.vwap_slippage_bps))
    print(f"\nclosest to market VWAP: {best.algo.upper()} ({best.vwap_slippage_bps:+.2f} bps)")


def cmd_results(args) -> None:
    from algo.analytics.results import run_results

    run_results(symbols=args.symbols or DEFAULT_SYMBOLS, n_days=args.days, pct_adv=args.pct_adv,
                lookback=args.lookback, exchange=args.exchange, synthetic=args.synthetic,
                out_dir=Path(args.out))


def cmd_mm(args) -> None:
    from algo.analytics.results import run_market_maker

    history = _history(args)
    day = args.date or _default_day(history)
    rep = run_market_maker(args.symbol, history, day, exchange=args.exchange,
                           chart=Path(args.chart) if args.chart else None)
    print(f"market maker {args.symbol} {day}")
    for k in ("pnl", "shares_traded", "fill_rate", "max_abs_position", "final_position"):
        v = rep[k]
        print(f"  {k:<18}{v:,.4f}" if isinstance(v, float) else f"  {k:<18}{v:,}")


def cmd_pairs(args) -> None:
    from algo.analytics.results import run_pairs

    rep = run_pairs(args.a, args.b, synthetic=args.synthetic, exchange=args.exchange,
                    chart=Path(args.chart) if args.chart else None)
    print(f"pairs {args.a}/{args.b}")
    for k in ("total_pnl", "sharpe", "max_drawdown", "trades"):
        print(f"  {k:<14}{rep[k]:,.4f}" if isinstance(rep[k], float) else f"  {k:<14}{rep[k]}")


def cmd_bench(args) -> None:
    from algo.analytics.bench import run_bench

    run_bench(repeats=args.repeats, days=args.days, out=Path(args.out) if args.out else None)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="algo", description="execution algos + backtester + TCA")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download and cache market data")
    f.add_argument("symbols", nargs="*")
    f.add_argument("--daily", action="store_true", help="daily bars instead of minute bars")
    f.set_defaults(func=cmd_fetch)

    def order_args(sp):
        sp.add_argument("--symbol", default="AAPL")
        sp.add_argument("--qty", type=int, default=200_000)
        sp.add_argument("--side", default="buy", choices=["buy", "sell"])
        sp.add_argument("--date", help="YYYY-MM-DD (default: last cached day)")
        sp.add_argument("--start", help="HH:MM, default 09:30")
        sp.add_argument("--end", help="HH:MM, default 16:00")
        sp.add_argument("--exchange", default="sim", choices=["sim", "cpp"])
        sp.add_argument("--lookback", type=int, default=20, help="days for the VWAP curve")
        sp.add_argument("--synthetic", action="store_true", help="use generated data")
        sp.add_argument("--seed", type=int, default=7)

    r = sub.add_parser("run", help="run one algo on one parent order")
    r.add_argument("algo", choices=["twap", "vwap", "pov", "aggressive"])
    order_args(r)
    r.add_argument("--chart", help="save a schedule-vs-volume chart here")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="TWAP vs VWAP vs POV on the same order")
    order_args(c)
    c.set_defaults(func=cmd_compare)

    res = sub.add_parser("results", help="batch run for the README results table")
    res.add_argument("symbols", nargs="*")
    res.add_argument("--days", type=int, default=10)
    res.add_argument("--pct-adv", type=float, default=0.01)
    res.add_argument("--lookback", type=int, default=20)
    res.add_argument("--exchange", default="sim", choices=["sim", "cpp"])
    res.add_argument("--synthetic", action="store_true")
    res.add_argument("--out", default="results")
    res.set_defaults(func=cmd_results)

    m = sub.add_parser("mm", help="market maker on one day")
    order_args(m)
    m.add_argument("--chart")
    m.set_defaults(func=cmd_mm)

    pr = sub.add_parser("pairs", help="pairs strategy on daily bars")
    pr.add_argument("a", nargs="?", default="KO")
    pr.add_argument("b", nargs="?", default="PEP")
    pr.add_argument("--exchange", default="sim", choices=["sim", "cpp"])
    pr.add_argument("--synthetic", action="store_true")
    pr.add_argument("--chart")
    pr.set_defaults(func=cmd_pairs)

    b = sub.add_parser("bench", help="backtest speed, Python vs C++ exchange")
    b.add_argument("--repeats", type=int, default=3)
    b.add_argument("--days", type=int, default=20)
    b.add_argument("--out")
    b.set_defaults(func=cmd_bench)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
