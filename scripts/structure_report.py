#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import sys
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
    _read_bridge_state,
    _snapshot_from_payload,
)
from trading_radar.filters import evaluate_tradeability
from trading_radar.market_session import detect_market_session
from trading_radar.market_structure import evaluate_market_structure
from trading_radar.metrics import atr_from_bars
from trading_radar.models import Bar, OrderCheck
from trading_radar.prop_challenge import evaluate_prop_challenge


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_file.json")
    parser.add_argument("--symbol", action="append", help="Limit report to one or more symbols.")
    parser.add_argument("--bars", type=int, default=24, help="Recent bars for range context.")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = _read_json(config_path)
    config["_config_path"] = str(config_path.resolve())
    try:
        state_path, state = _read_bridge_state(config)
    except BridgeStateError as exc:
        print(f"State unavailable: {exc}")
        raise SystemExit(0)

    account = _account_from_state(state["account"])
    server_time = _optional_float(state.get("server_time")) or time.time()
    payloads = {item["symbol"]: item for item in state.get("symbols", [])}
    selected = set(args.symbol or config["symbols"].keys())

    print(f"State: {state_path}")
    print(f"Generated: {state.get('ts', 'n/a')}")
    print(f"Account equity: {account.equity:.2f} {account.currency}".rstrip())
    print()

    for symbol, symbol_config in config["symbols"].items():
        if symbol not in selected or not symbol_config.get("enabled", True):
            continue
        payload = payloads.get(symbol)
        if payload is None:
            print(f"{symbol}: missing from state file")
            print()
            continue
        if not payload.get("ok", False):
            print(f"{symbol}: bridge error: {payload.get('error', 'unknown')}")
            print()
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
        tradeability = evaluate_tradeability(
            snapshot=snapshot,
            account=account,
            atr=atr,
            symbol_config=symbol_config,
            account_config=config["account"],
            order_check=order_check,
        )
        structure = evaluate_market_structure(snapshot, bars, tradeability)
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
        context = _context(bars, atr, args.bars)
        _print_symbol_report(symbol, snapshot, tradeability, structure, prop, session, context)


def _context(bars: list[Bar], atr: float | None, window: int) -> dict[str, Any]:
    if not bars:
        return {}
    recent = bars[-max(2, window) :]
    wider = bars[-80:]
    previous = bars[-72:-24] if len(bars) >= 72 else []
    recent_high = max(bar.high for bar in recent)
    recent_low = min(bar.low for bar in recent)
    last = bars[-1]
    range_width = recent_high - recent_low
    previous_width = max((max(bar.high for bar in previous) - min(bar.low for bar in previous)) if previous else 0.0, atr or 0.0)
    range_position = (last.close - recent_low) / range_width if range_width > 0 else 0.5
    swing_highs, swing_lows = _swings(wider)
    latest_range = max(last.high - last.low, abs(last.high - bars[-2].close), abs(last.low - bars[-2].close)) if len(bars) > 1 else last.high - last.low
    return {
        "bars": len(bars),
        "last_time": datetime.fromtimestamp(last.time, timezone.utc).isoformat(),
        "last_close": last.close,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "range_width": range_width,
        "range_position": max(0.0, min(1.0, range_position)),
        "atr": atr,
        "range_atr": range_width / atr if atr and atr > 0 else None,
        "compression": range_width / previous_width if previous_width > 0 else None,
        "latest_range_atr": latest_range / atr if atr and atr > 0 else None,
        "swing_highs": swing_highs[-3:],
        "swing_lows": swing_lows[-3:],
    }


def _swings(bars: list[Bar], width: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for index in range(width, len(bars) - width):
        window = bars[index - width : index + width + 1]
        if bars[index].high == max(bar.high for bar in window):
            highs.append((index, bars[index].high))
        if bars[index].low == min(bar.low for bar in window):
            lows.append((index, bars[index].low))
    return highs, lows


def _print_symbol_report(symbol, snapshot, tradeability, structure, prop, session, context) -> None:
    print(f"{symbol} / M15")
    print("=" * (len(symbol) + 6))
    print(f"Decision: {tradeability.decision} score={tradeability.score}/100")
    print(f"Market: {session.status} | {session.note}")
    print(f"Structure: {structure.structure} | bias={structure.bias} | confidence={structure.confidence}/100")
    print(f"Setup: {structure.setup}")
    print()
    print("Price / Cost")
    print(f"- last/mid: {_fmt(context.get('last_close'))}")
    print(f"- bid/ask: {_fmt(snapshot.bid)} / {_fmt(snapshot.ask)}")
    print(f"- tick age: {snapshot.tick_age_seconds:.1f}s")
    print(f"- spread/ATR: {_pct(tradeability.spread_to_atr)}")
    print(f"- ATR: {_fmt(context.get('atr'))}")
    print()
    print("Recent Range")
    print(f"- bars used: {context.get('bars', 0)}")
    print(f"- last closed bar: {context.get('last_time', 'n/a')}")
    print(f"- recent high/low: {_fmt(context.get('recent_high'))} / {_fmt(context.get('recent_low'))}")
    print(f"- range width: {_fmt(context.get('range_width'))} ({_ratio(context.get('range_atr'))} ATR)")
    print(f"- close position: {_pct(context.get('range_position'))}")
    print(f"- compression vs previous: {_pct(context.get('compression'))}")
    print(f"- latest candle range: {_ratio(context.get('latest_range_atr'))} ATR")
    print()
    print("Levels")
    print(f"- trigger: {_level(structure.trigger_level)}")
    print(f"- invalid: {_level(structure.invalid_level)}")
    print(f"- target: {_level(structure.target_level)}")
    print()
    print("Recent Swings")
    print(f"- highs: {_swings_text(context.get('swing_highs', []))}")
    print(f"- lows: {_swings_text(context.get('swing_lows', []))}")
    print()
    if prop is not None:
        print("PF / LucidFlex Proxy")
        print(f"- status: {prop.status}")
        print(f"- mode: {prop.mode}")
        print(f"- stop points: {_fmt(prop.stop_points)}")
        print(f"- 1 contract risk: {_money(prop.risk_one_contract)}")
        print(f"- aggressive risk: {_money(prop.risk_aggressive)}")
        print("- reasons:")
        for reason in prop.reasons:
            print(f"  - {reason}")
        print()
    print("Structure Reasons")
    for reason in structure.reasons:
        print(f"- {reason}")
    print()
    print("Risk Reasons")
    if tradeability.reasons:
        for reason in tradeability.reasons:
            print(f"- {reason}")
    else:
        print("- none")
    print()


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _level(value: float | None) -> str:
    return "waiting" if value is None else f"{value:.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def _ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _swings_text(swings: list[tuple[int, float]]) -> str:
    if not swings:
        return "n/a"
    return ", ".join(f"{price:.2f}" for _, price in swings)


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
