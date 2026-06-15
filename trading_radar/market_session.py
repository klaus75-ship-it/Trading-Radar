from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class MarketSession:
    status: str
    is_weekend: bool
    is_stale: bool
    note: str


def detect_market_session(
    now: datetime | None = None,
    tick_age_seconds: float | None = None,
    stale_threshold_seconds: float | None = None,
) -> MarketSession:
    current = now or datetime.now(timezone.utc)
    # Futures/FX are generally closed from late Friday to Sunday evening UTC.
    # This is a coarse safety label, not an exchange calendar.
    is_weekend = current.weekday() == 5 or (
        current.weekday() == 6 and current.hour < 22
    )
    is_stale = (
        tick_age_seconds is not None
        and stale_threshold_seconds is not None
        and tick_age_seconds > stale_threshold_seconds
    )
    if is_weekend:
        return MarketSession(
            status="週末/休市",
            is_weekend=True,
            is_stale=is_stale,
            note="週末結構回顧，不是即時入場訊號",
        )
    if is_stale:
        return MarketSession(
            status="報價過舊",
            is_weekend=False,
            is_stale=True,
            note="報價過舊，只做觀察，不做即時入場判斷",
        )
    return MarketSession(
        status="即時/可觀察",
        is_weekend=False,
        is_stale=False,
        note="報價仍在新鮮度門檻內",
    )
