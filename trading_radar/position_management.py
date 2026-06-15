from __future__ import annotations

from dataclasses import dataclass

from .market_structure import MarketStructureResult
from .models import PositionInfo, SymbolSnapshot, TradeabilityResult
from .structure_context import StructureContext


@dataclass(frozen=True)
class PositionManagementResult:
    status: str
    stage: str
    action: str
    side: str
    volume: float
    entry_price: float
    current_price: float
    profit: float
    unrealized_points: float
    stop_reference: float | None
    target_reference: float | None
    risk_note: str
    reasons: list[str]


def evaluate_position_management(
    snapshot: SymbolSnapshot,
    tradeability: TradeabilityResult,
    structure: MarketStructureResult,
    context: StructureContext | None,
    positions: list[PositionInfo],
) -> PositionManagementResult | None:
    symbol_positions = [position for position in positions if position.symbol == snapshot.symbol]
    if not symbol_positions:
        return None

    position = _primary_position(symbol_positions)
    mid = (snapshot.bid + snapshot.ask) / 2 if snapshot.bid > 0 and snapshot.ask > 0 else position.current_price
    side = position.side.upper()
    direction = 1.0 if side == "BUY" else -1.0
    points = (mid - position.entry_price) * direction
    atr = tradeability.atr or 0.0
    stop_reference = _stop_reference(structure, context, side)
    target_reference = structure.target_level
    reasons: list[str] = []

    if len(symbol_positions) > 1:
        reasons.append(f"同商品有 {len(symbol_positions)} 筆持倉，先以最大 volume 管理")

    if _is_against_structure(side, structure):
        stage = "AGAINST_STRUCTURE"
        action = "持倉方向和目前結構相反，優先檢查是否要降風險"
        reasons.append(f"持倉 {side}，但結構偏向 {structure.bias}")
    elif stop_reference is not None and _is_invalidated(mid, stop_reference, side):
        stage = "INVALID"
        action = "價格已穿過結構失效區，優先處理風險"
        reasons.append("現價已穿過結構失效參考位")
    elif atr > 0 and points >= atr * 1.5:
        stage = "PROTECT_PROFIT"
        action = "已有足夠浮盈，不建議加碼；可檢查停損是否能推到結構保護位"
        reasons.append(f"浮盈約 {points / atr:.1f} ATR")
    elif atr > 0 and points <= -atr * 0.8:
        stage = "DEFEND"
        action = "持倉進入防守區，避免加碼，等待是否收回結構內"
        reasons.append(f"浮虧約 {abs(points) / atr:.1f} ATR")
    elif structure.structure in {"高位消化", "低位消化"}:
        stage = "BOX_MANAGE"
        action = "在消化箱體內管理，不在箱體中段加碼"
        reasons.append(f"目前結構是 {structure.structure}")
    else:
        stage = "MANAGE"
        action = "持倉中，依結構失效位管理，不再看新進場訊號"
        reasons.append("偵測到既有持倉")

    return PositionManagementResult(
        status="持倉管理",
        stage=stage,
        action=action,
        side=side,
        volume=position.volume,
        entry_price=position.entry_price,
        current_price=mid,
        profit=position.profit,
        unrealized_points=points,
        stop_reference=stop_reference,
        target_reference=target_reference,
        risk_note=_risk_note(position, stop_reference, side),
        reasons=reasons,
    )


def _primary_position(positions: list[PositionInfo]) -> PositionInfo:
    return sorted(positions, key=lambda item: abs(item.volume), reverse=True)[0]


def _is_against_structure(side: str, structure: MarketStructureResult) -> bool:
    return (side == "BUY" and structure.bias == "偏空") or (side == "SELL" and structure.bias == "偏多")


def _is_invalidated(price: float, stop_reference: float, side: str) -> bool:
    if side == "BUY":
        return price <= stop_reference
    return price >= stop_reference


def _stop_reference(
    structure: MarketStructureResult,
    context: StructureContext | None,
    side: str,
) -> float | None:
    if structure.invalid_level is not None:
        return structure.invalid_level
    if context is None:
        return None
    return context.recent_low if side == "BUY" else context.recent_high


def _risk_note(position: PositionInfo, stop_reference: float | None, side: str) -> str:
    if stop_reference is None:
        return "沒有結構保護位，先不要加碼"
    if position.stop_loss is None:
        return f"目前未偵測到 SL；結構參考保護位 {stop_reference:.2f}"
    if side == "BUY" and position.stop_loss < stop_reference:
        return f"SL 在結構參考位下方；可檢查是否需要收緊到 {stop_reference:.2f}"
    if side == "SELL" and position.stop_loss > stop_reference:
        return f"SL 在結構參考位上方；可檢查是否需要收緊到 {stop_reference:.2f}"
    return "SL 已接近或優於結構參考位"
