from constants import FILE_NAME, SCORING_RULES

def get_region():
  if FILE_NAME == 'JP.csv':
    return 'JP'
  elif FILE_NAME == 'US.csv':
    return 'US'
  else:
    return 'Unknown'
  
def get_scoring_rules(data):
  target_sector = data.get('Sector')
  target_region = get_region()
  return [
    rule for rule in SCORING_RULES
    if (rule.get('sector') is None or rule.get('sector') == target_sector) and
       (rule.get('region') is None or rule.get('region') == target_region)
  ]

def scale_val(val, min_v, max_v, weight, reverse=False):
  """Helper to turn a raw number into a weighted score."""
  if val is None: return 0
  if not reverse:
    if val <= min_v: return 0
    if val >= max_v: return weight
    return ((val - min_v) / (max_v - min_v)) * weight
  else:
    if val >= max_v: return 0
    if val <= min_v: return weight
    return ((max_v - val) / (max_v - min_v)) * weight

def calc_score(data):
  total_score = 0
  total_weight = 0
  lost_weight = 0
  final_rules = get_scoring_rules(data)
  for rule in final_rules:
    val = data.get(rule['key'])
    total_weight += rule['weight']
    if val is not None:
      total_score += scale_val(val, rule['min'], rule['max'], rule['weight'], rule.get('rev', False))
    else:
      lost_weight += rule['weight']

  if lost_weight >= total_weight:
    return 0
  
  final_score = total_score * 100 / (total_weight - lost_weight)
  
  return round(final_score, 2)
