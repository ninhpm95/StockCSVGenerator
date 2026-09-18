from __future__ import annotations
from dataclasses import dataclass, fields

@dataclass
class ETFStats:
    etfs: int = 0
    holdings: int = 0
    matched: int = 0
    missed: int = 0
    # Holdings that matched a stock in the database but were discarded
    # before being counted as `matched` because their weight was missing
    # or non-positive. Tracked separately from `missed` (which means "no
    # stock found at all") so matched + missed + invalid_weight == holdings
    # always holds -- previously these fell into neither bucket and the
    # totals silently didn't add up.
    invalid_weight: int = 0
    matched_weight: float = 0.0

    def __iadd__(self, other: ETFStats) -> ETFStats:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))
        return self

    def log_summary(self, logger) -> None:
        logger.info("=" * 80)
        logger.info("DONE")
        logger.info("ETFs processed: %d", self.etfs)
        logger.info("Holdings encountered: %d", self.holdings)
        logger.info(
            "Stock matches: %d | Stock misses: %d | Matched but invalid weight: %d",
            self.matched,
            self.missed,
            self.invalid_weight,
        )
        logger.info("Total matched weight (sum across ETFs): %.2f", self.matched_weight)
        logger.info("=" * 80)
