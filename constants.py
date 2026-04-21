from fields import *

OUTPUT_DIR = "output"
FILE_NAME = "JP.csv"
COLUMNS_TO_PRESERVE = [TICKER, BOUGHT, NOTE]
FINAL_COLUMNS = [TICKER, NAME, BOUGHT, MARKET_CAP, PE_RATIO, FORWARD_PE_RATIO, PB, ROA, NET_MARGIN, OPERATING_MARGIN, DEBT_TO_EQUITY, EARNINGS_GROWTH, DIVIDEND_YIELD, PRICE_1D, PRICE_3D, PRICE_5D, VOL_1D, VOL_3D, VOL_5D, TARGET_HIGH_PERCENT, TARGET_LOW_PERCENT, TARGET_MEAN_PERCENT, CURRENT_PRICE, SECTOR, AVG_RATING, NOTE]

SCORING_RULES = [
  {'key': TARGET_MEAN_PERCENT,  'min': 0.0,   'max': 0.5,   'weight': 25, 'rev': False},
  {'key': MARKET_CAP,           'min': 1e11,  'max': 1e13,  'weight': 10, 'rev': False, 'region': 'JP'},
  {'key': MARKET_CAP,           'min': 1e10,  'max': 5e11,  'weight': 10, 'rev': False, 'region': 'US'},
  {'key': PE_RATIO,             'min': 10.0,  'max': 35.0,  'weight': 10, 'rev': True },
  {'key': PB,                   'min': 0.5,   'max': 1.5,   'weight': 10, 'rev': True,  'region': 'JP' },
  {'key': PB,                   'min': 1.0,   'max': 6.0,   'weight': 10, 'rev': True,  'region': 'US' },
  {'key': PEG,                  'min': 0.5,   'max': 2.5,   'weight': 10, 'rev': True },
  {'key': ROA,                  'min': 0.02,  'max': 0.12,  'weight': 10, 'rev': False},
  {'key': ROE,                  'min': 0.08,  'max': 0.25,  'weight': 5,  'rev': False, 'region': 'JP'},
  {'key': ROE,                  'min': 0.12,  'max': 0.40,  'weight': 5,  'rev': False, 'region': 'US'},
  {'key': NET_MARGIN,           'min': 0.04,  'max': 0.15,  'weight': 10, 'rev': False, 'region': 'JP'},
  {'key': NET_MARGIN,           'min': 0.08,  'max': 0.25,  'weight': 10, 'rev': False, 'region': 'US'},
  {'key': ROIC,                 'min': 0.05,  'max': 0.25,  'weight': 10, 'rev': False}
]
