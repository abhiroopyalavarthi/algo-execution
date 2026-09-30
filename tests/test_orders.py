from algo.core.orders import Bar, Side, to_price, to_ticks


def test_ticks_round_trip():
    assert to_ticks(229.99999) == 23000
    assert to_ticks(0.1 + 0.2) == 30
    assert to_price(12345) == 123.45


def test_typical_price_is_integer_ticks():
    b = Bar.make(None, "X", 10.00, 10.02, 9.99, 10.01, 500)
    assert (b.high, b.low, b.close) == (1002, 999, 1001)
    assert b.typical == 1001  # (1002 + 999 + 1001) / 3 = 1000.67 -> 1001


def test_side_signs():
    assert Side.BUY.sign == 1 and Side.SELL.sign == -1
    assert Side.BUY.opposite() is Side.SELL
