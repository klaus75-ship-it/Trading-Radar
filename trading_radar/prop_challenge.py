from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .market_structure import MarketStructureResult
from .models import SymbolSnapshot, TradeabilityResult


@dataclass(frozen=True)
class PropChallengeResult:
    enabled: bool
    status: str
    mode: str
    stop_points: float | None
    risk_one_contract: float | None
    risk_aggressive: float | None
    one_contracts: int
    aggressive_contracts: int
    reasons: list[str]


def evaluate_prop_challenge(
    snapshot: SymbolSnapshot,
    tradeability: TradeabilityResult,
    structure: MarketStructureResult,
    config: dict[str, Any],
) -> PropChallengeResult | None:
    if not config.get("enabled", False):
        return None
    symbols = config.get("symbols")
    if symbols is not None and snapshot.symbol not in set(symbols):
        return None

    one_contracts = int(config.get("standard_contracts", 1))
    aggressive_contracts = int(config.get("aggressive_contracts", 2))
    dollars_per_point = float(config.get("dollars_per_point", 10.0))
    min_stop_points = float(config.get("min_stop_points", 5.0))
    max_standard_stop_points = float(config.get("max_standard_stop_points", 15.0))
    max_aggressive_stop_points = float(config.get("max_aggressive_stop_points", 10.0))
    max_standard_risk = float(config.get("max_standard_risk", 150.0))
    max_aggressive_risk = float(config.get("max_aggressive_risk", 220.0))
    min_structure_confidence = int(config.get("min_structure_confidence", 65))
    aggressive_structure_confidence = int(config.get("aggressive_structure_confidence", 75))

    stop_points = _stop_points(snapshot, structure)
    risk_one = _risk(stop_points, dollars_per_point, one_contracts)
    risk_aggressive = _risk(stop_points, dollars_per_point, aggressive_contracts)
    reasons: list[str] = []

    if tradeability.decision != "OBSERVE":
        reasons.append("風控雷達未通過")
    if structure.structure in {"失序波動", "資料不足", "方向未確認"}:
        reasons.append(f"結構是 {structure.structure}，不適合進攻")
    if structure.trigger_level is None:
        reasons.append("沒有明確觸發位")
    if stop_points is None:
        reasons.append("無法計算停損距離")
    else:
        if stop_points < min_stop_points:
            reasons.append(f"停損 {stop_points:.1f} 點太窄，容易被雜訊掃掉")
        if stop_points > max_standard_stop_points:
            reasons.append(f"停損 {stop_points:.1f} 點超過標準上限 {max_standard_stop_points:.1f}")
    if structure.confidence < min_structure_confidence:
        reasons.append(f"結構信心 {structure.confidence} 低於標準門檻 {min_structure_confidence}")

    standard_allowed = not reasons and risk_one is not None and risk_one <= max_standard_risk
    if risk_one is not None and risk_one > max_standard_risk:
        reasons.append(f"1 口風險 {risk_one:.0f} 超過標準上限 {max_standard_risk:.0f}")
        standard_allowed = False

    aggressive_reasons = list(reasons)
    if structure.confidence < aggressive_structure_confidence:
        aggressive_reasons.append(
            f"結構信心 {structure.confidence} 低於加速門檻 {aggressive_structure_confidence}"
        )
    if stop_points is not None and stop_points > max_aggressive_stop_points:
        aggressive_reasons.append(
            f"停損 {stop_points:.1f} 點超過加速上限 {max_aggressive_stop_points:.1f}"
        )
    if risk_aggressive is not None and risk_aggressive > max_aggressive_risk:
        aggressive_reasons.append(
            f"{aggressive_contracts} 口風險 {risk_aggressive:.0f} 超過加速上限 {max_aggressive_risk:.0f}"
        )

    aggressive_allowed = (
        standard_allowed
        and len(aggressive_reasons) == 0
        and risk_aggressive is not None
        and risk_aggressive <= max_aggressive_risk
    )

    if aggressive_allowed:
        return PropChallengeResult(
            enabled=True,
            status="PF 加速模式允許",
            mode=f"{aggressive_contracts} 口進攻",
            stop_points=stop_points,
            risk_one_contract=risk_one,
            risk_aggressive=risk_aggressive,
            one_contracts=one_contracts,
            aggressive_contracts=aggressive_contracts,
            reasons=["結構、停損距離與 2 口風險都在加速條件內"],
        )

    if standard_allowed:
        return PropChallengeResult(
            enabled=True,
            status="PF 標準模式可觀察",
            mode=f"{one_contracts} 口標準",
            stop_points=stop_points,
            risk_one_contract=risk_one,
            risk_aggressive=risk_aggressive,
            one_contracts=one_contracts,
            aggressive_contracts=aggressive_contracts,
            reasons=aggressive_reasons or ["標準模式可做，但加速條件不足"],
        )

    return PropChallengeResult(
        enabled=True,
        status="PF 暫不允許",
        mode="等待更乾淨的位置",
        stop_points=stop_points,
        risk_one_contract=risk_one,
        risk_aggressive=risk_aggressive,
        one_contracts=one_contracts,
        aggressive_contracts=aggressive_contracts,
        reasons=reasons,
    )


def _stop_points(snapshot: SymbolSnapshot, structure: MarketStructureResult) -> float | None:
    if structure.trigger_level is not None and structure.invalid_level is not None:
        return abs(structure.trigger_level - structure.invalid_level)
    if structure.invalid_level is not None:
        mid = (snapshot.bid + snapshot.ask) / 2
        return abs(mid - structure.invalid_level)
    return None


def _risk(stop_points: float | None, dollars_per_point: float, contracts: int) -> float | None:
    if stop_points is None:
        return None
    return stop_points * dollars_per_point * contracts
