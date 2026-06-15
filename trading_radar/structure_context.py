from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Bar


@dataclass(frozen=True)
class StructureContext:
    bars_count: int
    last_bar_time: str
    last_close: float
    recent_high: float
    recent_low: float
    range_width: float
    range_position: float
    atr: float | None
    range_atr: float | None
    compression: float | None
    latest_range_atr: float | None


def build_structure_context(
    bars: list[Bar],
    atr: float | None,
    window: int = 24,
) -> StructureContext | None:
    if not bars:
        return None
    recent = bars[-max(2, window) :]
    previous = bars[-72:-24] if len(bars) >= 72 else []
    last = bars[-1]
    recent_high = max(bar.high for bar in recent)
    recent_low = min(bar.low for bar in recent)
    range_width = recent_high - recent_low
    previous_width = max(
        (max(bar.high for bar in previous) - min(bar.low for bar in previous)) if previous else 0.0,
        atr or 0.0,
    )
    range_position = (last.close - recent_low) / range_width if range_width > 0 else 0.5
    latest_range = (
        max(last.high - last.low, abs(last.high - bars[-2].close), abs(last.low - bars[-2].close))
        if len(bars) > 1
        else last.high - last.low
    )
    return StructureContext(
        bars_count=len(bars),
        last_bar_time=datetime.fromtimestamp(last.time, timezone.utc).isoformat(),
        last_close=last.close,
        recent_high=recent_high,
        recent_low=recent_low,
        range_width=range_width,
        range_position=max(0.0, min(1.0, range_position)),
        atr=atr,
        range_atr=range_width / atr if atr and atr > 0 else None,
        compression=range_width / previous_width if previous_width > 0 else None,
        latest_range_atr=latest_range / atr if atr and atr > 0 else None,
    )
