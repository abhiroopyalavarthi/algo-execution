machine: aarch64 Linux 6.8.0-138-generic, Python 3.12.14

End to end (TWAP + VWAP + market maker, 20 days each, best of 3):

| exchange | bars | seconds | bars/sec |
|---|---:|---:|---:|
| python | 23,400 | 0.370 | 63,169 |
| cpp | 23,400 | 0.363 | 64,521 |

C++ / Python: 1.02x

Exchange only (~90 orders per bar, best of 3):

| exchange | bars | orders | fills | seconds | bars/sec | orders/sec |
|---|---:|---:|---:|---:|---:|---:|
| python | 2,000 | 181,000 | 12,086 | 0.371 | 5,388 | 487,601 |
| cpp | 2,000 | 181,000 | 12,086 | 0.255 | 7,835 | 709,070 |

C++ / Python: 1.45x
