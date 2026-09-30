machine: arm64 Darwin 27.0.0, Python 3.12.14

End to end (TWAP + VWAP + market maker, 20 days each, best of 3):

| exchange | bars | seconds | bars/sec |
|---|---:|---:|---:|
| python | 23,400 | 0.365 | 64,038 |
| cpp | 23,400 | 0.337 | 69,501 |

C++ / Python: 1.09x

Exchange only (~90 orders per bar, best of 3):

| exchange | bars | orders | fills | seconds | bars/sec | orders/sec |
|---|---:|---:|---:|---:|---:|---:|
| python | 2,000 | 181,000 | 12,086 | 0.408 | 4,901 | 443,547 |
| cpp | 2,000 | 181,000 | 12,086 | 0.251 | 7,969 | 721,183 |

C++ / Python: 1.63x
