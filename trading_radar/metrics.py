from __future__ import annotations

from .models import Bar


def atr_from_bars(bars: list[Bar], period: int) -> float | None:
    if len(bars) < period + 1:
        return None

    true_ranges: list[float] = []
    for index in range(1, len(bars)):
        high = bars[index].high
        low = bars[index].low
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    recent = true_ranges[-period:]
    return sum(recent) / len(recent)

