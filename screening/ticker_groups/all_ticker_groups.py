from .banks_jp import BANKS_JP
from .ex_financials_jp import EX_FINANCIALS_JP
from .gold import GOLD
from .nasdaq100 import NASDAQ100
from .nikkei225 import NIKKEI225
from .nikkei300 import NIKKEI300
from .nikkei400 import NIKKEI400
from .reit_jp import REIT_JP
from .semiconductor_jp import SEMICONDUCTOR_JP
from .semiconductor_us import SEMICONDUCTOR_US
from .silver import SILVER
from .sp500_equal_weight import SP500_EQUAL_WEIGHT
from .sp500 import SP500
from .topix import TOPIX

ALL_TICKER_GROUPS = {
  "TOPIX": TOPIX,
  "Nikkei 225": NIKKEI225,
  "Nikkei 300": NIKKEI300,
  "Nikkei 400": NIKKEI400,
  "S&P 500": SP500,
  "S&P 500 equal weight": SP500_EQUAL_WEIGHT,
  "Nasdaq 100": NASDAQ100,
  "JP Semiconductor": SEMICONDUCTOR_JP,
  "US Semiconductor": SEMICONDUCTOR_US,
  "JP banks": BANKS_JP,
  "JP ex-financials": EX_FINANCIALS_JP,
  "JP REIT": REIT_JP,
  "Gold": GOLD,
  "Silver": SILVER
}
