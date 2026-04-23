from typing import List, Optional, Any

def safe_div(numerator: Optional[float], denominator: Optional[float], default: Any = None) -> Any:
  try:
    if numerator is None or denominator is None or denominator == 0:
      return default
    return numerator / denominator
  except (TypeError, ValueError):
    return default

def calculate_price_trends(current: float, history: List[float]):
  if not current or not history or len(history) < 6:
    return None, None, None
  hp1 = safe_div(current - history[-2], history[-2])
  hp3 = safe_div(current - history[-4], history[-4])
  hp5 = safe_div(current - history[-6], history[-6])
  return hp1, hp3, hp5

def calculate_volume_surges(volume_data: List[int]):
  if not volume_data or len(volume_data) <= 5:
    return None, None, None
  n = len(volume_data)
  # Logic preserved exactly as requested
  base1 = sum(volume_data[:-1]) / (n - 1)
  recent1 = volume_data[-1]
  avg_last_1 = safe_div(recent1 - base1, base1, default=0)

  base3 = sum(volume_data[:-3]) / (n - 3)
  recent3 = sum(volume_data[-3:-1]) / 2
  avg_last_3 = safe_div(recent3 - base3, base3, default=0)

  base5 = sum(volume_data[:-5]) / (n - 5)
  recent5 = sum(volume_data[-5:-1]) / 4
  avg_last_5 = safe_div(recent5 - base5, base5, default=0)
  return avg_last_1, avg_last_3, avg_last_5
