from typing import List, Optional, Any

def safe_div(numerator: Optional[float], denominator: Optional[float], default: Any = None) -> Any:
    try:
        if numerator is None or denominator is None or denominator == 0:
            return default
        # NaN is the only value that's never equal to itself, so `x != x`
        # catches it regardless of whether x is a Python float or a
        # numpy.float64 (isinstance(x, float) isn't reliable for the latter
        # across numpy versions).
        if numerator != numerator or denominator != denominator:
            return default
        return numerator / denominator
    except (TypeError, ValueError):
        return default

def calculate_price_trends(current: float, history: List[float]):
    # NOTE: history[-N-1] (not history[-N]) is used for the N-day-ago price
    # below, because history[-1] is equivalent to `current` (yfinance's
    # last history row is today's in-progress price), so it's skipped to
    # avoid comparing `current` against itself.
    if not isinstance(current, (int, float)) or current < 0 or not isinstance(history, list) or len(history) <= 30:
        return (None,) * 8
    hp1  = safe_div(current - history[-2],  history[-2])
    hp3  = safe_div(current - history[-4],  history[-4])
    hp5  = safe_div(current - history[-6],  history[-6])
    hp7  = safe_div(current - history[-8],  history[-8])
    hp10 = safe_div(current - history[-11], history[-11])
    hp15 = safe_div(current - history[-16], history[-16])
    hp20 = safe_div(current - history[-21], history[-21])
    hp30 = safe_div(current - history[-31], history[-31])
    return hp1, hp3, hp5, hp7, hp10, hp15, hp20, hp30

def calculate_volume_surges(volume_data: List[int]):
    # NOTE: today's volume (volume_data[-1]) is a partial figure while the
    # market is still open, so it's deliberately excluded from the N-day
    # "recent" windows below. avg_last_1 is the one exception - it's
    # specifically meant to measure today's (possibly partial) volume
    # against the baseline, so it uses volume_data[-1] on purpose.
    #
    # For N in {3, 5, 30}: recent_N is the N most recently *completed* days,
    # i.e. volume_data[-(N+1):-1] (N elements, today excluded). base_N
    # excludes that same (N+1)-wide span - today plus the N recent days -
    # from the overall average, so the two don't overlap.
    if not isinstance(volume_data, list) or len(volume_data) <= 31:
        return (None,) * 4
    n = len(volume_data)

    base1 = sum(volume_data[:-1]) / (n - 1)
    recent1 = volume_data[-1]
    avg_last_1 = safe_div(recent1 - base1, base1, default=0)

    base3 = sum(volume_data[:-4]) / (n - 4)
    recent3 = sum(volume_data[-4:-1]) / 3
    avg_last_3 = safe_div(recent3 - base3, base3, default=0)

    base5 = sum(volume_data[:-6]) / (n - 6)
    recent5 = sum(volume_data[-6:-1]) / 5
    avg_last_5 = safe_div(recent5 - base5, base5, default=0)

    base30 = sum(volume_data[:-31]) / (n - 31)
    recent30 = sum(volume_data[-31:-1]) / 30
    avg_last_30 = safe_div(recent30 - base30, base30, default=0)

    return avg_last_1, avg_last_3, avg_last_5, avg_last_30
