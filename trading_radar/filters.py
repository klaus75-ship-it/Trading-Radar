from __future__ import annotations

from .models import AccountInfo, OrderCheck, SymbolSnapshot, TradeabilityResult
from .position_sizer import size_by_risk


def evaluate_tradeability(
    snapshot: SymbolSnapshot,
    account: AccountInfo,
    atr: float | None,
    symbol_config: dict,
    account_config: dict,
    order_check: OrderCheck | None,
) -> TradeabilityResult:
    reasons: list[str] = []
    score = 100

    if atr is None or atr <= 0:
        return TradeabilityResult(
            symbol=snapshot.symbol,
            decision="REJECT",
            score=0,
            reasons=["ATR unavailable"],
            atr=atr,
            spread_to_atr=None,
            stop_distance=None,
            suggested_volume=0.0,
            margin_required=None,
            risk_budget=None,
            min_volume_loss=None,
            min_volume_risk_fraction=None,
            min_account_equity_required=None,
        )

    spread_to_atr = snapshot.spread / atr
    stop_distance = atr * float(symbol_config["stop_atr_multiple"])
    risk_fraction = float(account_config["risk_per_trade"])
    risk_budget = account.equity * risk_fraction
    loss_per_lot = _loss_for_volume(
        volume=1.0,
        stop_distance_price=stop_distance,
        tick_value=snapshot.tick_value,
        tick_size=snapshot.tick_size,
    )
    min_volume_loss = _loss_for_volume(
        volume=snapshot.volume_min,
        stop_distance_price=stop_distance,
        tick_value=snapshot.tick_value,
        tick_size=snapshot.tick_size,
    )
    min_volume_risk_fraction = (
        min_volume_loss / account.equity if account.equity > 0 and min_volume_loss is not None else None
    )
    min_account_equity_required = (
        min_volume_loss / risk_fraction if risk_fraction > 0 and min_volume_loss is not None else None
    )
    suggested_volume = size_by_risk(
        account_equity=account.equity,
        risk_fraction=risk_fraction,
        stop_distance_price=stop_distance,
        tick_value=snapshot.tick_value,
        tick_size=snapshot.tick_size,
        volume_min=snapshot.volume_min,
        volume_step=snapshot.volume_step,
        volume_max=snapshot.volume_max,
    )

    if snapshot.tick_age_seconds > float(symbol_config["max_tick_age_seconds"]):
        reasons.append(f"stale tick: {snapshot.tick_age_seconds:.1f}s")
        score -= 30

    if spread_to_atr > float(symbol_config["max_spread_to_atr"]):
        reasons.append(f"spread/ATR too high: {spread_to_atr:.2%}")
        score -= 40

    min_stop_distance = snapshot.trade_stops_level * snapshot.point
    if stop_distance < min_stop_distance:
        reasons.append("stop distance below broker stops level")
        score -= 40

    if suggested_volume <= 0:
        target_text = f"{risk_fraction:.2%}"
        min_risk_text = _format_pct(min_volume_risk_fraction)
        required_text = _format_money(min_account_equity_required, account.currency)
        reasons.append(
            f"minimum lot risk {min_risk_text} exceeds target {target_text}"
            + (f"; target-sized equity ~= {required_text}" if required_text else "")
        )
        score -= 50

        allow_min_lot = bool(account_config.get("allow_min_lot_when_over_budget", False))
        max_min_lot_risk = float(account_config.get("max_min_lot_risk", 0.0))
        if (
            allow_min_lot
            and min_volume_risk_fraction is not None
            and min_volume_risk_fraction <= max_min_lot_risk
        ):
            suggested_volume = snapshot.volume_min
            reasons.append("using minimum lot because it is within hard risk cap")
            score += 20

    margin_required = order_check.margin if order_check is not None else None
    if order_check is not None and not order_check.ok:
        reasons.append(f"order check failed: {order_check.comment or order_check.retcode}")
        score -= 50

    if margin_required is not None and account.margin_free > 0:
        max_margin = account.margin_free * float(account_config["max_margin_usage"])
        if margin_required > max_margin:
            reasons.append(f"margin too high: {margin_required:.2f} > {max_margin:.2f}")
            score -= 30

    score = max(0, score)
    min_score = int(symbol_config.get("min_score", 70))
    decision = "OBSERVE" if score >= min_score and not reasons else "REJECT"

    return TradeabilityResult(
        symbol=snapshot.symbol,
        decision=decision,
        score=score,
        reasons=reasons,
        atr=atr,
        spread_to_atr=spread_to_atr,
        stop_distance=stop_distance,
        suggested_volume=suggested_volume,
        margin_required=margin_required,
        risk_budget=risk_budget,
        min_volume_loss=min_volume_loss,
        min_volume_risk_fraction=min_volume_risk_fraction,
        min_account_equity_required=min_account_equity_required,
    )


def _loss_for_volume(
    volume: float,
    stop_distance_price: float,
    tick_value: float,
    tick_size: float,
) -> float | None:
    if volume <= 0 or stop_distance_price <= 0 or tick_value <= 0 or tick_size <= 0:
        return None
    return (stop_distance_price / tick_size) * tick_value * volume


def _format_pct(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.2%}"


def _format_money(value: float | None, currency: str) -> str:
    if value is None:
        return ""
    suffix = f" {currency}" if currency else ""
    return f"{value:.2f}{suffix}"
