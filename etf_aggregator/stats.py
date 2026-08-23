from __future__ import annotations
from dataclasses import dataclass, fields

@dataclass
class ETFStats:
    etfs: int = 0
    holdings: int = 0
    matched: int = 0
    missed: int = 0
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
        logger.info("Stock matches: %d | Stock misses: %d", self.matched, self.missed)
        logger.info("=" * 80)
