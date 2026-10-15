# Financial updates: market configuration

`data/exchanges.csv` controls market routing. Each input row's `Exchange` is
matched case-insensitively against `exchange`, `short_name`, or a Yahoo exchange
code. `lookup_name` is also accepted when it resolves unambiguously. The matched
row's `country` takes precedence over the input filename. Missing, unknown, or
ambiguous exchanges fall back to the filename's country code (for example
`JP_stocks.csv`). A missing or ambiguous country stops the update before writing.

The input `Exchange` column is retained in the output. Identical tickers on
different exchanges remain separate listings. Tickers are read as strings to
preserve leading zeros and literal tickers such as `NA`.

## Configuration columns

| Column | Meaning | Examples |
| --- | --- | --- |
| `yahoo_suffix` | Yahoo Finance exchange suffix; blank means no suffix; `N/A` means not configured/supported by this task | `.T`, `.HK`, `.KS`, `.KQ` |
| `yahoo_exchange_codes` | Yahoo `info.exchange` aliases, separated by `\|` | `NMS\|NGM\|NCM\|BTS` |
| `yahoo_dot_replacement` | Optional replacement for dots in the ticker before adding its suffix | `-` for US listings (`BRK.B` becomes `BRK-B`) |
| `tv_screener` | TradingView scanner market name; blank skips technical ratings | `japan`, `hongkong`, `america` |
| `tv_exchange` | TradingView exchange prefix; blank skips technical ratings | `TSE`, `HKEX`, `NASDAQ`, `AMEX` |

Keep these settings consistent across rows representing aliases of the same
exchange. No new Python mapping is needed when adding a configured market.
Yahoo suffixes already present on a ticker are not appended twice.

Without an exchange, country fallback works only when the relevant setting is
unambiguous across that country's configured exchanges. For example, Japan has
`.T`, while China requires an exchange to distinguish `.SS` from `.SZ`.
Unconfigured Yahoo markets yield missing financial values with a logged warning;
they are never sent as guessed, unsuffixed US symbols. Derivatives exchanges and
unlisted instruments are left with `N/A` rather than assigned equity suffixes.

TradingView uses the input exchange first, or Yahoo's reported exchange when the
input has no configured TradingView prefix. Symbols are grouped by screener
within each batch; each group gets separate daily and weekly requests. Results
retain input order. Fast mode skips all TradingView requests.

Reference for suffix maintenance:
[Yahoo Finance exchanges and data providers](https://uk.help.yahoo.com/kb/exchanges-data-providers-yahoo-finance-sln2310.html).
TradingView request format:
[tradingview_ta batch analysis documentation](https://python-tradingview-ta.readthedocs.io/en/stable/usage.html#retrieving-multiple-analysis).
