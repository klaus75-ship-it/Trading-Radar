from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Bar:
    time: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: float = 0.0
    spread: float = 0.0


@dataclass(frozen=True)
class AccountInfo:
    balance: float
    equity: float
    margin_free: float
    margin_level: float | None = None
    currency: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AccountInfo":
        return cls(
            balance=float(payload.get("balance", 0.0)),
            equity=float(payload.get("equity", 0.0)),
            margin_free=float(payload.get("margin_free", 0.0)),
            margin_level=_optional_float(payload.get("margin_level")),
            currency=str(payload.get("currency", "")),
        )


@dataclass(frozen=True)
class SymbolSnapshot:
    timestamp: datetime
    symbol: str
    bid: float
    ask: float
    spread: float
    spread_points: float
    tick_age_seconds: float
    point: float
    digits: int
    volume_min: float
    volume_step: float
    volume_max: float
    tick_value: float
    tick_size: float
    contract_size: float
    trade_stops_level: int
    trade_freeze_level: int
    swap_long: float
    swap_short: float
    trade_mode: int

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SymbolSnapshot":
        tick_time = float(payload.get("tick_time", 0.0))
        now = datetime.now(timezone.utc)
        tick_age = max(0.0, now.timestamp() - tick_time) if tick_time > 0 else 999999.0
        point = float(payload.get("point", 0.0))
        bid = float(payload.get("bid", 0.0))
        ask = float(payload.get("ask", 0.0))
        spread = float(payload.get("spread", ask - bid))

        return cls(
            timestamp=now,
            symbol=str(payload["symbol"]),
            bid=bid,
            ask=ask,
            spread=spread,
            spread_points=spread / point if point > 0 else 0.0,
            tick_age_seconds=tick_age,
            point=point,
            digits=int(payload.get("digits", 0)),
            volume_min=float(payload.get("volume_min", 0.0)),
            volume_step=float(payload.get("volume_step", 0.0)),
            volume_max=float(payload.get("volume_max", 0.0)),
            tick_value=float(payload.get("tick_value", 0.0)),
            tick_size=float(payload.get("tick_size", 0.0)),
            contract_size=float(payload.get("contract_size", 0.0)),
            trade_stops_level=int(payload.get("trade_stops_level", 0)),
            trade_freeze_level=int(payload.get("trade_freeze_level", 0)),
            swap_long=float(payload.get("swap_long", 0.0)),
            swap_short=float(payload.get("swap_short", 0.0)),
            trade_mode=int(payload.get("trade_mode", 0)),
        )


@dataclass(frozen=True)
class OrderCheck:
    ok: bool
    retcode: int
    margin: float | None
    comment: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OrderCheck":
        return cls(
            ok=bool(payload.get("check_ok", payload.get("ok", False))),
            retcode=int(payload.get("retcode", -1)),
            margin=_optional_float(payload.get("margin")),
            comment=str(payload.get("comment", "")),
        )


@dataclass(frozen=True)
class TradeabilityResult:
    symbol: str
    decision: str
    score: int
    reasons: list[str]
    atr: float | None
    spread_to_atr: float | None
    stop_distance: float | None
    suggested_volume: float
    margin_required: float | None
    risk_budget: float | None = None
    min_volume_loss: float | None = None
    min_volume_risk_fraction: float | None = None
    min_account_equity_required: float | None = None


@dataclass(frozen=True)
class PositionInfo:
    symbol: str
    side: str
    volume: float
    entry_price: float
    current_price: float
    profit: float
    stop_loss: float | None = None
    take_profit: float | None = None
    ticket: int | None = None
    magic: int | None = None
    comment: str = ""


def bar_from_payload(payload: dict[str, Any]) -> Bar:
    return Bar(
        time=int(payload["time"]),
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        tick_volume=float(payload.get("tick_volume", 0.0)),
        spread=float(payload.get("spread", 0.0)),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
