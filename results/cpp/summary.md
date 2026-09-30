# Results (real minute bars from Yahoo)

50 parent orders (AAPL, MSFT, NVDA, AMZN, JPM; 1% of ADV each, random side), each run with TWAP, VWAP and POV. Exchange: cpp. Slippage in bps, positive = cost.

| algo   |   orders |   vs VWAP mean |   vs VWAP |mean| |   vs VWAP std |   vs arrival mean |   shortfall mean |   tracking err % |   fill rate % |
|:-------|---------:|---------------:|-----------------:|--------------:|------------------:|-----------------:|-----------------:|--------------:|
| twap   |       50 |          -0.87 |            10.24 |         15.70 |              5.35 |             5.35 |            11.76 |        100.00 |
| vwap   |       50 |           0.20 |             6.61 |          9.91 |              6.45 |             6.45 |             5.41 |        100.00 |
| pov    |       50 |          -2.08 |            11.76 |         17.89 |              4.19 |             4.20 |            13.34 |         99.98 |

Mean VWAP slippage by symbol (bps):

| symbol   |   twap |   vwap |    pov |
|:---------|-------:|-------:|-------:|
| AAPL     |   3.85 |   1.55 |   3.08 |
| AMZN     |   1.67 |  -1.02 |  -4.94 |
| JPM      |  -8.52 |  -6.98 | -11.28 |
| MSFT     |  -1.63 |   4.58 |   4.44 |
| NVDA     |   0.30 |   2.86 |  -1.72 |

VWAP finished closer to the market VWAP than TWAP on 35 of 50 orders (mean |slippage| 6.61 vs 10.24 bps).
