from __future__ import annotations

import math


def round_volume(volume: float, step: float, minimum: float, maximum: float) -> float:
    if step <= 0 or minimum <= 0 or maximum <= 0:
        return 0.0
    if volume < minimum:
        return 0.0
    rounded = math.floor(volume / step) * step
    precision = max(0, _decimal_places(step))
    return round(min(max(rounded, minimum), maximum), precision)


def size_by_risk(
    account_equity: float,
    risk_fraction: float,
    stop_distance_price: float,
    tick_value: float,
    tick_size: float,
    volume_min: float,
    volume_step: float,
    volume_max: float,
) -> float:
    risk_money = account_equity * risk_fraction
    if risk_money <= 0 or stop_distance_price <= 0 or tick_value <= 0 or tick_size <= 0:
        return 0.0

    loss_per_lot = (stop_distance_price / tick_size) * tick_value
    if loss_per_lot <= 0:
        return 0.0

    return round_volume(risk_money / loss_per_lot, volume_step, volume_min, volume_max)


def _decimal_places(value: float) -> int:
    text = f"{value:.10f}".rstrip("0")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])

