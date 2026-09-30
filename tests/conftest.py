import pandas as pd
import pytest

from algo.data.loader import synthetic_minute


@pytest.fixture(scope="session")
def history():
    """25 days of seeded synthetic minute bars."""
    return synthetic_minute("SYN", days=25, seed=3)


@pytest.fixture(scope="session")
def last_day(history):
    return sorted(set(history.index.date))[-1]


@pytest.fixture(scope="session")
def day_bars(history, last_day):
    return history[history.index.date == last_day]


def make_bars(rows, start="2026-09-25 09:30", symbol_tz="America/New_York"):
    """rows: list of (open, high, low, close, volume) in dollars."""
    idx = pd.date_range(start, periods=len(rows), freq="1min", tz=symbol_tz)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)
