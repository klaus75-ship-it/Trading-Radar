#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_radar.app_file import (
    BridgeStateError,
    _account_from_state,
    _bar_from_payload,
    _closed_bars,
    _optional_float,
    _position_from_payload,
    _read_bridge_state,
    _snapshot_from_payload,
)
from trading_radar.filters import evaluate_tradeability
from trading_radar.market_session import detect_market_session
from trading_radar.market_structure import evaluate_market_structure
from trading_radar.metrics import atr_from_bars
from trading_radar.models import OrderCheck
from trading_radar.position_management import evaluate_position_management
from trading_radar.prop_challenge import evaluate_prop_challenge
from trading_radar.structure_context import build_structure_context
from trading_radar.telegram_notifier import TelegramConfig, TelegramNotifier
from trading_radar.telegram_notifier import _stage_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_file.json")
    parser.add_argument("--send-telegram", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = _read_json(config_path)
    config["_config_path"] = str(config_path.resolve())
    try:
        message = build_brief(config_path, config)
    except BridgeStateError as exc:
        message = f"每日雷達摘要\n資料狀態: stale/unavailable\n原因: {exc}"
    print(message)

    if args.send_telegram:
        _load_env(config_path.resolve().parent / ".env")
        telegram = dict(config.get("telegram", {}))
        telegram["enabled"] = True
        notifier = TelegramNotifier(TelegramConfig.from_config({"telegram": telegram}, config_path.resolve().parent))
        notifier._send(message)


def build_brief(config_path: Path, config: dict[str, Any]) -> str:
    state_path, state = _read_bridge_state(config)
    account = _account_from_state(state["account"])
    server_time = _optional_float(state.get("server_time")) or time.time()
    state_mtime = state_path.stat().st_mtime
    payloads = {item["symbol"]: item for item in state.get("symbols", [])}
    positions = [_position_from_payload(item) for item in state.get("positions", [])]

    lines = [
        "每日雷達摘要",
        f"資料: {state.get('ts', 'n/a')}",
        f"帳戶 equity: {account.equity:.2f} {account.currency}".rstrip(),
        "",
    ]

    for symbol, symbol_config in config["symbols"].items():
        if not symbol_config.get("enabled", True):
            continue
        payload = payloads.get(symbol)
        if payload is None:
            lines.extend([f"{symbol}: 暫不交易", "- bridge missing from state file", ""])
            continue
        if not payload.get("ok", False):
            lines.extend([f"{symbol}: 暫不交易", f"- bridge error: {payload.get('error', 'unknown')}", ""])
            continue

        bars = _closed_bars(
            [_bar_from_payload(item) for item in payload.get("bars", [])],
            str(config.get("file_bridge", {}).get("timeframe", "M15")),
            server_time,
        )
        snapshot = _snapshot_from_payload(
            payload,
            state_mtime,
            server_time,
            bars,
            float(symbol_config["max_tick_age_seconds"]),
        )
        atr = atr_from_bars(bars, int(symbol_config["atr_period"]))
        order_check = OrderCheck(
            ok=True,
            retcode=0,
            margin=_optional_float(payload.get("margin_buy_min")),
            comment="file bridge margin estimate for min volume",
        )
        tradeability = evaluate_tradeability(
            snapshot=snapshot,
            account=account,
            atr=atr,
            symbol_config=symbol_config,
            account_config=config["account"],
            order_check=order_check,
        )
        structure = evaluate_market_structure(snapshot, bars, tradeability)
        context = build_structure_context(bars, atr)
        session = detect_market_session(
            tick_age_seconds=snapshot.tick_age_seconds,
            stale_threshold_seconds=float(symbol_config["max_tick_age_seconds"]),
        )
        prop = evaluate_prop_challenge(
            snapshot,
            tradeability,
            structure,
            symbol_config.get("prop_challenge", config.get("prop_challenge", {})),
        )
        management = evaluate_position_management(snapshot, tradeability, structure, context, positions)
        lines.extend(_symbol_lines(symbol, tradeability, structure, session, prop, management))

    return "\n".join(lines).rstrip()


def _symbol_lines(symbol, tradeability, structure, session, prop, management) -> list[str]:
    if management is not None:
        return [
            f"{symbol}: 持倉管理",
            f"- 市場: {session.status}",
            f"- 持倉: {management.side} {management.volume:g} lot @ {_level(management.entry_price)}",
            f"- 現價: {_level(management.current_price)} | 浮動: {management.unrealized_points:.2f} 點 / {_money(management.profit)}",
            f"- 階段: {_stage_text(management.stage)}",
            f"- 建議: {management.action}",
            f"- 保護位: {_level(management.stop_reference)} | 目標: {_level(management.target_reference)}",
            "",
        ]
    status = _display_status(tradeability, structure, prop)
    lines = [
        f"{symbol}: {status}",
        f"- 市場: {session.status}",
        f"- 結構: {structure.structure} / {structure.bias} / {structure.confidence}/100",
        f"- 做法: {structure.setup}",
        f"- 觸發: {_level(structure.trigger_level)} | 失效: {_level(structure.invalid_level)} | 目標: {_level(structure.target_level)}",
    ]
    if prop is not None:
        lines.extend(
            [
                f"- PF: {prop.status}",
                f"- 1口風險: {_money(prop.risk_one_contract)} | 2口風險: {_money(prop.risk_aggressive)}",
            ]
        )
    reasons = list(tradeability.reasons)
    if session.is_weekend and "週末/休市" not in reasons:
        reasons.append("週末/休市，只做結構回顧")
    if reasons:
        lines.append("- 原因: " + "；".join(reasons[:3]))
    lines.append("")
    return lines


def _level(value: float | None) -> str:
    return "等待確認" if value is None else f"{value:.2f}"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _display_status(tradeability, structure, prop) -> str:
    if tradeability.decision != "OBSERVE":
        return "暫不交易"
    if structure.trigger_level is None:
        return "只觀察"
    if prop is not None and prop.status == "PF 暫不允許":
        return "只觀察"
    return "等條件成形"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def _resolve_path(config_path: Path, target_path: str) -> Path:
    path = Path(target_path)
    if path.is_absolute():
        return path
    return config_path.resolve().parent / path


if __name__ == "__main__":
    main()
