from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from .filters import evaluate_tradeability
from .market_structure import evaluate_market_structure
from .market_session import detect_market_session
from .metrics import atr_from_bars
from .models import AccountInfo, Bar, OrderCheck, SymbolSnapshot
from .prop_challenge import evaluate_prop_challenge
from .storage import Storage
from .structure_context import build_structure_context
from .telegram_notifier import TelegramConfig, TelegramNotifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_file.json")
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    config = _read_json(Path(args.config))
    config["_config_path"] = str(Path(args.config).resolve())
    base_dir = Path(args.config).resolve().parent
    storage = Storage(str(_resolve_path(args.config, config["storage"]["sqlite_path"])))
    notifier = TelegramNotifier(TelegramConfig.from_config(config, base_dir))

    try:
        while True:
            run_once(config, storage, notifier)
            if args.run_once or config["scan"].get("run_once", False):
                break
            time.sleep(float(config["scan"]["interval_seconds"]))
    finally:
        storage.close()


def run_once(config: dict[str, Any], storage: Storage, notifier: TelegramNotifier) -> None:
    try:
        state_path, state = _read_bridge_state(config)
    except BridgeStateError as exc:
        print(f"bridge state unavailable: {exc}")
        return

    account = _account_from_state(state["account"])
    symbols = {item["symbol"]: item for item in state.get("symbols", [])}
    server_time = _optional_float(state.get("server_time")) or time.time()

    for symbol, symbol_config in config["symbols"].items():
        if not symbol_config.get("enabled", True):
            continue
        payload = symbols.get(symbol)
        if payload is None:
            reason = "bridge missing from state file"
            print(f"{symbol} REJECT {reason}")
            storage.save_bridge_event(symbol, reason, account, config)
            continue
        if not payload.get("ok", False):
            reason = f"bridge error: {payload.get('error', 'unknown')}"
            print(f"{symbol} REJECT {reason}")
            storage.save_bridge_event(symbol, reason, account, config)
            continue

        snapshot = _snapshot_from_payload(payload)
        bars = _closed_bars(
            [_bar_from_payload(item) for item in payload.get("bars", [])],
            str(config.get("file_bridge", {}).get("timeframe", "M15")),
            server_time,
        )
        atr = atr_from_bars(bars, int(symbol_config["atr_period"]))
        order_check = OrderCheck(
            ok=True,
            retcode=0,
            margin=_optional_float(payload.get("margin_buy_min")),
            comment="file bridge margin estimate for min volume",
        )
        result = evaluate_tradeability(
            snapshot=snapshot,
            account=account,
            atr=atr,
            symbol_config=symbol_config,
            account_config=config["account"],
            order_check=order_check,
        )
        structure = evaluate_market_structure(snapshot, bars, result)
        context = build_structure_context(bars, atr)
        session = detect_market_session(
            tick_age_seconds=snapshot.tick_age_seconds,
            stale_threshold_seconds=float(symbol_config["max_tick_age_seconds"]),
        )
        prop = evaluate_prop_challenge(
            snapshot,
            result,
            structure,
            symbol_config.get("prop_challenge", config.get("prop_challenge", {})),
        )
        storage.save_scan(snapshot, result, account, structure, prop, config)
        _print_result(result, structure, prop, session)
        notifier.notify_result(result, structure, prop, session, context)


def _account_from_state(payload: dict[str, Any]) -> AccountInfo:
    return AccountInfo(
        balance=float(payload.get("balance", 0.0)),
        equity=float(payload.get("equity", 0.0)),
        margin_free=float(payload.get("margin_free", 0.0)),
        margin_level=_optional_float(payload.get("margin_level")),
        currency=str(payload.get("currency", "")),
    )


def _snapshot_from_payload(payload: dict[str, Any]) -> SymbolSnapshot:
    point = float(payload.get("point", 0.0))
    bid = float(payload.get("bid", 0.0))
    ask = float(payload.get("ask", 0.0))
    spread = float(payload.get("spread", ask - bid))
    tick_time = float(payload.get("tick_time", 0.0))
    now = datetime.now(timezone.utc)
    tick_age = max(0.0, now.timestamp() - tick_time) if tick_time > 0 else 999999.0
    return SymbolSnapshot(
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


def _bar_from_payload(payload: dict[str, Any]) -> Bar:
    return Bar(
        time=int(payload["time"]),
        open=float(payload["open"]),
        high=float(payload["high"]),
        low=float(payload["low"]),
        close=float(payload["close"]),
        tick_volume=float(payload.get("tick_volume", 0.0)),
        spread=float(payload.get("spread", 0.0)),
    )


def _closed_bars(bars: list[Bar], timeframe: str, server_time: float) -> list[Bar]:
    if not bars:
        return bars
    timeframe_seconds = _timeframe_seconds(timeframe)
    if timeframe_seconds is None:
        return bars[:-1] if len(bars) > 1 else bars
    last_bar = bars[-1]
    if last_bar.time + timeframe_seconds > server_time:
        return bars[:-1]
    return bars


def _timeframe_seconds(timeframe: str) -> int | None:
    mapping = {
        "M1": 60,
        "M5": 300,
        "M15": 900,
        "M30": 1800,
        "H1": 3600,
        "H4": 14400,
        "D1": 86400,
    }
    return mapping.get(timeframe.upper())


def _check_state_age(path: Path, max_age_seconds: float) -> None:
    age = time.time() - path.stat().st_mtime
    if age > max_age_seconds:
        raise BridgeStateError(f"state file is stale: {age:.1f}s > {max_age_seconds:.1f}s ({path})")


class BridgeStateError(RuntimeError):
    pass


def _read_bridge_state(config: dict[str, Any], require_fresh: bool = True) -> tuple[Path, dict[str, Any]]:
    config_path = config.get("_config_path", "config_file.json")
    state_path = _resolve_path(config_path, config["file_bridge"]["state_file"])
    state = _read_state_json(state_path, config)
    if require_fresh:
        _check_state_age(state_path, float(config["scan"]["max_state_age_seconds"]))
    return state_path, state


def _read_state_json(path: Path, config: dict[str, Any]) -> dict[str, Any]:
    file_bridge = config.get("file_bridge", {})
    attempts = int(file_bridge.get("read_attempts", 5))
    retry_seconds = float(file_bridge.get("read_retry_seconds", 0.05))
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return _read_json(path)
        except (JSONDecodeError, OSError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(retry_seconds)
    raise BridgeStateError(f"cannot read complete JSON from {path}: {last_error}")


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _resolve_path(config_path: str, target_path: str) -> Path:
    path = Path(target_path)
    if path.is_absolute():
        return path
    return Path(config_path).resolve().parent / path


def _print_result(result, structure=None, prop=None, session=None) -> None:
    spread_text = "n/a" if result.spread_to_atr is None else f"{result.spread_to_atr:.2%}"
    min_risk_text = "n/a" if result.min_volume_risk_fraction is None else f"{result.min_volume_risk_fraction:.2%}"
    required_text = (
        "n/a"
        if result.min_account_equity_required is None
        else f"{result.min_account_equity_required:.2f}"
    )
    structure_text = "" if structure is None else (
        f" structure={structure.structure} bias={structure.bias} "
        f"trigger={structure.trigger_level} invalid={structure.invalid_level}"
    )
    prop_text = "" if prop is None else (
        f" prop={prop.status} mode={prop.mode} risk1={prop.risk_one_contract}"
    )
    session_text = "" if session is None else f" session={session.status}"
    print(
        f"{result.symbol} {result.decision} score={result.score} "
        f"spread/ATR={spread_text} volume={result.suggested_volume} "
        f"minLotRisk={min_risk_text} targetEquity={required_text} "
        f"margin={result.margin_required}{structure_text}{prop_text}{session_text} reasons={result.reasons}"
    )


if __name__ == "__main__":
    main()
