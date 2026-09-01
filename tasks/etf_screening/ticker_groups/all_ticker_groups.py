from .banks_jp import BANKS_JP
from .ex_financials_jp import EX_FINANCIALS_JP
from .gold import GOLD
from .jpx_prime_150 import JPX_PRIME_150
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
from .US_bonds_0_3_years import US_BONDS_0_3_YEARS
from .US_bonds_7_10_years import US_BONDS_7_10_YEARS
from .US_bonds_20_years import US_BONDS_20_YEARS

ALL_TICKER_GROUPS = {
    "TOPIX": TOPIX,
    "JPX Prime 150": JPX_PRIME_150,
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
    "Silver": SILVER,
    "US bonds 0-3 years": US_BONDS_0_3_YEARS,
    "US bonds 7-10 years": US_BONDS_7_10_YEARS,
    "US bonds 20 years": US_BONDS_20_YEARS,
}
