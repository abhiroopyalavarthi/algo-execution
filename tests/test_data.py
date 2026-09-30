import pandas as pd

from algo.data import loader


def test_cache_round_trip_offline(tmp_path):
    """Write synthetic days into a cache the same way fetch does, then load 20 days back."""
    df = loader.synthetic_minute("AAPL", days=22)
    out = tmp_path / "minute" / "AAPL"
    out.mkdir(parents=True)
    for d, part in df.groupby(df.index.date):
        part.to_parquet(out / f"{d}.parquet")
    days = loader.cached_days("AAPL", tmp_path)
    assert len(days) == 22
    back = loader.load_minute("AAPL", start=days[2], cache_dir=tmp_path)
    assert len(set(back.index.date)) == 20
    assert len(back) == 20 * 390
    assert str(back.index.tz) == "America/New_York"


def test_clean_handles_yfinance_shape():
    idx = pd.date_range("2026-09-25 13:30", periods=3, freq="1min", tz="UTC")
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Adj Close", "Volume"],
                                       ["AAPL"]])
    raw = pd.DataFrame([[1, 2, 0.5, 1.5, 1.5, 100]] * 3, index=idx, columns=cols)
    df = loader._clean(raw)
    assert list(df.columns) == loader.COLUMNS
    assert df.index[0].hour == 9 and df.index[0].minute == 30
    assert len(loader.regular_hours(df)) == 3


def test_missing_cache_raises(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        loader.load_minute("NOPE", cache_dir=tmp_path)
