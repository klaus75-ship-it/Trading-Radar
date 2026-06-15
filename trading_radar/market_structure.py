from __future__ import annotations

from dataclasses import dataclass

from .models import Bar, SymbolSnapshot, TradeabilityResult


@dataclass(frozen=True)
class MarketStructureResult:
    symbol: str
    structure: str
    bias: str
    setup: str
    confidence: int
    trigger_level: float | None
    invalid_level: float | None
    target_level: float | None
    reasons: list[str]


def evaluate_market_structure(
    snapshot: SymbolSnapshot,
    bars: list[Bar],
    tradeability: TradeabilityResult,
) -> MarketStructureResult:
    if len(bars) < 60 or tradeability.atr is None or tradeability.atr <= 0:
        return MarketStructureResult(
            symbol=snapshot.symbol,
            structure="資料不足",
            bias="中性",
            setup="等待更多 K 線",
            confidence=0,
            trigger_level=None,
            invalid_level=None,
            target_level=None,
            reasons=["M15 K 線或 ATR 不足"],
        )

    recent = bars[-80:]
    current = bars[-24:]
    previous = bars[-72:-24]
    closes = [bar.close for bar in recent]
    last_close = closes[-1]
    atr = tradeability.atr

    recent_high = max(bar.high for bar in current)
    recent_low = min(bar.low for bar in current)
    previous_high = max(bar.high for bar in previous) if previous else recent_high
    previous_low = min(bar.low for bar in previous) if previous else recent_low
    range_width = max(recent_high - recent_low, 0.0)
    previous_width = max(previous_high - previous_low, atr)
    range_position = _range_position(last_close, recent_low, recent_high)
    move_atr = (last_close - closes[0]) / atr
    compression_ratio = range_width / previous_width if previous_width > 0 else 1.0
    latest_range_atr = _true_range(bars[-1], bars[-2].close) / atr

    swing_highs, swing_lows = _swings(recent)
    higher_highs = _rising([price for _, price in swing_highs[-3:]])
    higher_lows = _rising([price for _, price in swing_lows[-3:]])
    lower_highs = _falling([price for _, price in swing_highs[-3:]])
    lower_lows = _falling([price for _, price in swing_lows[-3:]])

    if latest_range_atr >= 2.3:
        return _disorder(snapshot.symbol, latest_range_atr)

    live_price = _mid_price(snapshot, last_close)

    if abs(move_atr) >= 2.2 and range_position >= 0.55 and (higher_highs or higher_lows):
        support = _last_price(swing_lows) or recent_low
        pullback_high = support + atr * 0.6
        pullback_distance_atr = (live_price - support) / atr
        if live_price > pullback_high:
            return MarketStructureResult(
                symbol=snapshot.symbol,
                structure="趨勢延續",
                bias="偏多",
                setup=f"等回踩到 {_round_price(support)}-{_round_price(pullback_high)} 後重新站穩，不追高",
                confidence=_confidence(68 + min(abs(move_atr) * 3, 12) - max(compression_ratio - 1.0, 0) * 10),
                trigger_level=None,
                invalid_level=_round_price(support - atr * 0.25),
                target_level=None,
                reasons=[
                    f"近段推進約 {move_atr:.1f} ATR",
                    "swing 結構偏向抬高",
                    f"現價離回踩區約 {pullback_distance_atr:.1f} ATR，追高風險偏大",
                ],
            )
        trigger = max(live_price + atr * 0.15, support + atr * 0.25)
        risk = max(trigger - (support - atr * 0.25), atr)
        return MarketStructureResult(
            symbol=snapshot.symbol,
            structure="趨勢延續",
            bias="偏多",
            setup="回踩已接近，等重新站穩再看",
            confidence=_confidence(70 + min(abs(move_atr) * 4, 15) - max(compression_ratio - 1.0, 0) * 10),
            trigger_level=_round_price(trigger),
            invalid_level=_round_price(support - atr * 0.25),
            target_level=_round_price(trigger + risk),
            reasons=[
                f"近段推進約 {move_atr:.1f} ATR",
                "swing 結構偏向抬高",
                f"現價回到回踩區附近，距支撐 {pullback_distance_atr:.1f} ATR",
            ],
        )

    if abs(move_atr) >= 2.2 and range_position <= 0.45 and (lower_highs or lower_lows):
        resistance = _last_price(swing_highs) or recent_high
        pullback_low = resistance - atr * 0.6
        pullback_distance_atr = (resistance - live_price) / atr
        if live_price < pullback_low:
            return MarketStructureResult(
                symbol=snapshot.symbol,
                structure="趨勢延續",
                bias="偏空",
                setup=f"等反彈到 {_round_price(pullback_low)}-{_round_price(resistance)} 後重新轉弱，不追空",
                confidence=_confidence(68 + min(abs(move_atr) * 3, 12) - max(compression_ratio - 1.0, 0) * 10),
                trigger_level=None,
                invalid_level=_round_price(resistance + atr * 0.25),
                target_level=None,
                reasons=[
                    f"近段推進約 {move_atr:.1f} ATR",
                    "swing 結構偏向降低",
                    f"現價離反彈區約 {pullback_distance_atr:.1f} ATR，追空風險偏大",
                ],
            )
        trigger = min(live_price - atr * 0.15, resistance - atr * 0.25)
        risk = max((resistance + atr * 0.25) - trigger, atr)
        return MarketStructureResult(
            symbol=snapshot.symbol,
            structure="趨勢延續",
            bias="偏空",
            setup="反彈已接近，等重新轉弱再看",
            confidence=_confidence(70 + min(abs(move_atr) * 4, 15) - max(compression_ratio - 1.0, 0) * 10),
            trigger_level=_round_price(trigger),
            invalid_level=_round_price(resistance + atr * 0.25),
            target_level=_round_price(trigger - risk),
            reasons=[
                f"近段推進約 {move_atr:.1f} ATR",
                "swing 結構偏向降低",
                f"現價回到反彈區附近，距壓力 {pullback_distance_atr:.1f} ATR",
            ],
        )

    if compression_ratio <= 0.65 and range_width <= atr * 4.0:
        bias = "偏多" if range_position >= 0.65 else "偏空" if range_position <= 0.35 else "中性"
        return MarketStructureResult(
            symbol=snapshot.symbol,
            structure="壓縮待突破",
            bias=bias,
            setup="等區間邊界被接受後再看，不提前猜方向",
            confidence=_confidence(62 + (0.65 - compression_ratio) * 45),
            trigger_level=_round_price(recent_high if bias != "偏空" else recent_low),
            invalid_level=_round_price(recent_low if bias != "偏空" else recent_high),
            target_level=_round_price(recent_high + range_width if bias != "偏空" else recent_low - range_width),
            reasons=[
                f"近 24 根區間寬度縮到前段 {compression_ratio:.0%}",
                f"區間高低: {_round_price(recent_high)} / {_round_price(recent_low)}",
            ],
        )

    if abs(move_atr) < 1.5 and 0.25 <= range_position <= 0.75:
        return MarketStructureResult(
            symbol=snapshot.symbol,
            structure="區間震盪",
            bias="中性",
            setup="只看上下緣反應，區間中間不追",
            confidence=58,
            trigger_level=None,
            invalid_level=_round_price(recent_low),
            target_level=_round_price(recent_high),
            reasons=[
                f"近段方向只有 {move_atr:.1f} ATR",
                f"目前位於區間中段 {range_position:.0%}",
            ],
        )

    bias = "偏多" if range_position > 0.6 or move_atr > 1.0 else "偏空" if range_position < 0.4 or move_atr < -1.0 else "中性"
    return MarketStructureResult(
        symbol=snapshot.symbol,
        structure="方向未確認",
        bias=bias,
        setup="只觀察，等待更清楚的突破、回踩或邊界反應",
        confidence=45,
        trigger_level=None,
        invalid_level=_round_price(recent_low if bias != "偏空" else recent_high),
        target_level=None,
        reasons=[
            f"近段推進 {move_atr:.1f} ATR，尚未形成乾淨結構",
            f"收盤位於近 24 根區間 {range_position:.0%}",
        ],
    )


def _disorder(symbol: str, latest_range_atr: float) -> MarketStructureResult:
    return MarketStructureResult(
        symbol=symbol,
        structure="失序波動",
        bias="中性",
        setup="不做入場提醒，等待波動恢復秩序",
        confidence=75,
        trigger_level=None,
        invalid_level=None,
        target_level=None,
        reasons=[f"最新 K 線 range 約 {latest_range_atr:.1f} ATR，容易假突破"],
    )


def _swings(bars: list[Bar], width: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for index in range(width, len(bars) - width):
        window = bars[index - width : index + width + 1]
        high = bars[index].high
        low = bars[index].low
        if high == max(bar.high for bar in window):
            highs.append((index, high))
        if low == min(bar.low for bar in window):
            lows.append((index, low))
    return highs, lows


def _true_range(bar: Bar, previous_close: float) -> float:
    return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))


def _range_position(price: float, low: float, high: float) -> float:
    width = high - low
    if width <= 0:
        return 0.5
    return min(1.0, max(0.0, (price - low) / width))


def _rising(values: list[float]) -> bool:
    return len(values) >= 2 and all(values[index] > values[index - 1] for index in range(1, len(values)))


def _falling(values: list[float]) -> bool:
    return len(values) >= 2 and all(values[index] < values[index - 1] for index in range(1, len(values)))


def _last_price(swings: list[tuple[int, float]]) -> float | None:
    if not swings:
        return None
    return swings[-1][1]


def _mid_price(snapshot: SymbolSnapshot, fallback: float) -> float:
    if snapshot.bid > 0 and snapshot.ask > 0:
        return (snapshot.bid + snapshot.ask) / 2
    return fallback


def _confidence(value: float) -> int:
    return max(0, min(100, int(round(value))))


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)
