"""Market data: yfinance downloads cached as parquet, plus synthetic bars.

Yahoo only serves the last ~30 days of 1-minute bars (and at most 8 days per
request), so `fetch_minute` pulls it in chunks and writes one parquet file per
symbol per day under data/cache/minute/<SYMBOL>/<YYYY-MM-DD>.parquet. Days
already cached are never overwritten, so running fetch every week or two keeps
growing the history past Yahoo's window. Everything after that reads the cache
and works offline.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

NY = "America/New_York"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache"
COLUMNS = ["open", "high", "low", "close", "volume"]


def _clean(df: pd.DataFrame, intraday: bool = True) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):  # yfinance sometimes adds a ticker level
        df = df.droplevel(1, axis=1) if df.columns.nlevels > 1 else df
    df = df.rename(columns=str.lower)[COLUMNS].copy()
    idx = pd.DatetimeIndex(df.index)
    if intraday:
        idx = idx.tz_localize("UTC") if idx.tz is None else idx
        idx = idx.tz_convert(NY)
    else:  # daily bars: just the date
        idx = pd.DatetimeIndex(idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    df.index = idx
    df.index.name = "ts"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0).astype("int64")
    return df


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Keep 09:30 <= t < 16:00."""
    t = df.index.hour * 60 + df.index.minute
    return df[(t >= 570) & (t < 960)]


def fetch_minute(symbols: list[str], cache_dir: Path = DEFAULT_CACHE, days: int = 29) -> dict:
    """Download the last `days` days of 1-minute bars and cache any new full days."""
    import yfinance as yf

    written: dict[str, list[str]] = {}
    end = datetime.now()
    today = end.date()
    for sym in symbols:
        frames = []
        chunk_end = end
        start = end - timedelta(days=days)
        while chunk_end > start:  # yahoo allows 8 days per 1m request
            chunk_start = max(start, chunk_end - timedelta(days=7))
            raw = yf.download(sym, start=chunk_start.date(), end=chunk_end.date() + timedelta(days=1),
                              interval="1m", progress=False, auto_adjust=False, prepost=False)
            if raw is not None and len(raw):
                frames.append(_clean(raw))
            chunk_end = chunk_start
        if not frames:
            print(f"{sym}: no data returned")
            continue
        df = regular_hours(pd.concat(frames).sort_index())
        df = df[~df.index.duplicated(keep="last")]
        out_dir = cache_dir / "minute" / sym
        out_dir.mkdir(parents=True, exist_ok=True)
        for d, day_df in df.groupby(df.index.date):
            if d >= today:  # today's session may still be running
                continue
            path = out_dir / f"{d}.parquet"
            if not path.exists():
                day_df.to_parquet(path)
                written.setdefault(sym, []).append(str(d))
        print(f"{sym}: {len(written.get(sym, []))} new days, {len(cached_days(sym, cache_dir))} cached")
    return written


def fetch_daily(symbols: list[str], cache_dir: Path = DEFAULT_CACHE, period: str = "2y") -> None:
    import yfinance as yf

    out_dir = cache_dir / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        raw = yf.download(sym, period=period, interval="1d", progress=False, auto_adjust=True)
        if raw is None or not len(raw):
            print(f"{sym}: no daily data returned")
            continue
        df = _clean(raw, intraday=False)
        df.to_parquet(out_dir / f"{sym}.parquet")
        print(f"{sym}: {len(df)} daily bars")


def cached_days(symbol: str, cache_dir: Path = DEFAULT_CACHE) -> list[date]:
    d = cache_dir / "minute" / symbol
    if not d.exists():
        return []
    return sorted(date.fromisoformat(p.stem) for p in d.glob("*.parquet"))


def load_minute(
    symbol: str,
    start: date | str | None = None,
    end: date | str | None = None,
    cache_dir: Path = DEFAULT_CACHE,
) -> pd.DataFrame:
    """Minute bars for [start, end] (inclusive dates) from the cache only."""
    days = cached_days(symbol, cache_dir)
    if start is not None:
        days = [d for d in days if d >= pd.Timestamp(start).date()]
    if end is not None:
        days = [d for d in days if d <= pd.Timestamp(end).date()]
    if not days:
        raise FileNotFoundError(
            f"no cached minute bars for {symbol} in that range; run `algo fetch {symbol}` first"
        )
    parts = [pd.read_parquet(cache_dir / "minute" / symbol / f"{d}.parquet") for d in days]
    return pd.concat(parts).sort_index()


def load_daily(symbol: str, cache_dir: Path = DEFAULT_CACHE) -> pd.DataFrame:
    path = cache_dir / "daily" / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"no daily bars for {symbol}; run `algo fetch --daily {symbol}`")
    return pd.read_parquet(path)


# --- synthetic data ------------------------------------------------------------

def intraday_profile(n: int = 390) -> np.ndarray:
    """U-shaped volume profile: heavy open, quiet midday, heavy close."""
    m = np.arange(n)
    shape = 1.0 + 3.0 * np.exp(-m / 25.0) + 2.0 * np.exp(-(n - 1 - m) / 15.0)
    return shape / shape.sum()


def synthetic_minute(
    symbol: str = "SYN",
    days: int = 25,
    start: str = "2026-08-03",
    price: float = 200.0,
    daily_volume: float = 40e6,
    seed: int = 7,
) -> pd.DataFrame:
    """Seeded fake minute bars with a U-shaped volume curve and random-walk prices."""
    rng = np.random.default_rng(seed)
    profile = intraday_profile()
    sessions = pd.bdate_range(start, periods=days)
    frames = []
    px = price
    for d in sessions:
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=390, freq="1min", tz=NY)
        day_vol = daily_volume * rng.lognormal(0, 0.25)
        vol = day_vol * profile * rng.lognormal(0, 0.35, 390)
        # 4 ticks inside each minute to get a high and a low
        steps = rng.normal(0, 0.0006 / 2, (390, 4)) * px
        path = px + np.cumsum(steps.ravel()).reshape(390, 4)
        opens = np.concatenate([[px], path[:-1, -1]])
        highs = np.maximum(path.max(axis=1), opens)
        lows = np.minimum(path.min(axis=1), opens)
        closes = path[:, -1]
        frames.append(pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes,
             "volume": vol.astype("int64")}, index=idx).round({"open": 2, "high": 2, "low": 2,
                                                                "close": 2}))
        px = closes[-1] * (1 + rng.normal(0, 0.005))  # overnight gap
    df = pd.concat(frames)
    df.index.name = "ts"
    return df


def synthetic_pair(days: int = 500, seed: int = 11) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two daily price series whose log spread is mean-reverting (an AR(1))."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-09-02", periods=days)
    log_b = np.log(150) + np.cumsum(rng.normal(0, 0.012, days))
    spread = np.zeros(days)
    for t in range(1, days):
        spread[t] = 0.93 * spread[t - 1] + rng.normal(0, 0.006)
    log_a = np.log(60) + 0.8 * (log_b - np.log(150)) + spread

    def frame(logp):
        c = np.exp(logp)
        o = c * (1 + rng.normal(0, 0.002, days))
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.004,
                             "low": np.minimum(o, c) * 0.996, "close": c,
                             "volume": rng.integers(5_000_000, 12_000_000, days)},
                            index=pd.DatetimeIndex(idx, name="ts")).round(2)

    return frame(log_a), frame(log_b)
