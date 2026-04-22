from constants import FILE_NAME

def get_region():
  if FILE_NAME == 'JP.csv':
    return 'JP'
  elif FILE_NAME == 'US.csv':
    return 'US'
  else:
    return 'Unknown'
