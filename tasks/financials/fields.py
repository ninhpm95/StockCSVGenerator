"""
Centralized field names for the stock analysis tool.
Using a class with static attributes provides better namespacing.
"""

# Identifiers
TICKER = 'Ticker'
NAME = 'Name'
ISIN = 'ISIN'
SECTOR = 'Sector'

# Valuation Metrics
MARKET_CAP = 'Market cap'
PE_RATIO = 'PE ratio'
FORWARD_PE_RATIO = 'Forward PE ratio'
PB = 'PB'
PEG = 'PEG'

# Profitability & Health
ROA = 'ROA'
ROE = 'ROE'
ROIC = 'ROIC'
NET_MARGIN = 'Net Margin'
OPERATING_MARGIN = 'Operating Margin'
DEBT_TO_EQUITY = 'Debt To Equity'
CURRENT_RATIO = 'Current Ratio'
TOTAL_CASH_PER_SHARE = 'Total Cash Per Share'
EARNINGS_GROWTH = 'Earnings Growth'
PAYOUT_RATIO = 'Payout Ratio'
DIVIDEND_YIELD = 'Dividend yield'

# Momentum & Volume
AVG_VOLUME = 'Avg Volume'
VOL_1D = 'Vol 1D'
VOL_3D = 'Vol 3D'
VOL_5D = 'Vol 5D'
VOL_30D = 'Vol 30D'
PRICE_1D = 'Price 1D'
PRICE_3D = 'Price 3D'
PRICE_5D = 'Price 5D'
PRICE_7D = 'Price 7D'
PRICE_10D = 'Price 10D'
PRICE_15D = 'Price 15D'
PRICE_20D = 'Price 20D'
PRICE_30D = 'Price 30D'

# Analyst Targets
CURRENT_PRICE = 'Current price'
TARGET_HIGH = 'Target high'
TARGET_LOW = 'Target low'
TARGET_MEAN = 'Target mean'
TARGET_HIGH_PERCENT = 'Target high %'
TARGET_LOW_PERCENT = 'Target low %'
TARGET_MEAN_PERCENT = 'Target mean %'

# Ratings
AVG_RATING_1D = 'Avg Rating 1D'
AVG_RATING_7D = 'Avg Rating 7D'
AVG_RATING_1M = 'Avg Rating 1M'
AVG_RATING = 'Avg Rating'
AVG_RATING_SCORE = 'Avg Rating Score'
AVG_RATING_LABEL = 'Avg Rating Label'
GROWTH = 'Growth'
SCORE = 'Score'

# Others
FEE = 'Fee'
TER = 'TER'
BOUGHT = 'Bought'
NOTE = 'Note'
FAST = 'Fast'
NORMAL = 'Normal'
UPDATE_FINANCIALS = 'Update Financials'
FILTER_ETFS = 'Filter ETFs'
