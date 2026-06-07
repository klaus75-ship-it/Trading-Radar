from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .filters import evaluate_tradeability
from .metrics import atr_from_bars
from .models import AccountInfo, Bar, OrderCheck, SymbolSnapshot
from .storage import Storage
from .telegram_notifier import TelegramConfig, TelegramNotifier


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_file.json")
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    config = _read_json(Path(args.config))
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
    state_path = Path(config["file_bridge"]["state_file"])
    state = _read_json(state_path)
    _check_state_age(state_path, float(config["scan"]["max_state_age_seconds"]))

    account = _account_from_state(state["account"])
    symbols = {item["symbol"]: item for item in state.get("symbols", [])}

    for symbol, symbol_config in config["symbols"].items():
        if not symbol_config.get("enabled", True):
            continue
        payload = symbols.get(symbol)
        if payload is None:
            print(f"{symbol} REJECT missing from state file")
            continue
        if not payload.get("ok", False):
            print(f"{symbol} REJECT bridge_error={payload.get('error', 'unknown')}")
            continue

        snapshot = _snapshot_from_payload(payload)
        bars = [_bar_from_payload(item) for item in payload.get("bars", [])]
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
        storage.save_scan(snapshot, result)
        _print_result(result)
        notifier.notify_result(result)


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


def _check_state_age(path: Path, max_age_seconds: float) -> None:
    age = time.time() - path.stat().st_mtime
    if age > max_age_seconds:
        raise SystemExit(f"state file is stale: {age:.1f}s > {max_age_seconds:.1f}s ({path})")


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


def _print_result(result) -> None:
    spread_text = "n/a" if result.spread_to_atr is None else f"{result.spread_to_atr:.2%}"
    min_risk_text = "n/a" if result.min_volume_risk_fraction is None else f"{result.min_volume_risk_fraction:.2%}"
    required_text = (
        "n/a"
        if result.min_account_equity_required is None
        else f"{result.min_account_equity_required:.2f}"
    )
    print(
        f"{result.symbol} {result.decision} score={result.score} "
        f"spread/ATR={spread_text} volume={result.suggested_volume} "
        f"minLotRisk={min_risk_text} targetEquity={required_text} "
        f"margin={result.margin_required} reasons={result.reasons}"
    )


if __name__ == "__main__":
    main()
