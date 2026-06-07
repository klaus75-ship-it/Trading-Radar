from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .bridge_server import BridgeError, BridgeServer
from .filters import evaluate_tradeability
from .metrics import atr_from_bars
from .storage import Storage


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def run_once(bridge: BridgeServer, storage: Storage, config: dict) -> None:
    account = bridge.account()
    for symbol, symbol_config in config["symbols"].items():
        if not symbol_config.get("enabled", True):
            continue

        snapshot = bridge.snapshot(symbol)
        bars = bridge.bars(
            symbol=symbol,
            timeframe=symbol_config["atr_timeframe"],
            count=max(200, int(symbol_config["atr_period"]) + 2),
        )
        atr = atr_from_bars(bars, int(symbol_config["atr_period"]))

        order_check = None
        if atr is not None and atr > 0:
            provisional_stop = atr * float(symbol_config["stop_atr_multiple"])
            order_check = bridge.order_check(
                symbol=symbol,
                side="buy",
                volume=snapshot.volume_min,
                price=snapshot.ask,
                stop_distance=provisional_stop,
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    sqlite_path = _resolve_sqlite_path(args.config, config["storage"]["sqlite_path"])
    storage = Storage(str(sqlite_path))

    bridge_config = config["bridge"]
    bridge = BridgeServer(
        host=bridge_config["host"],
        port=int(bridge_config["port"]),
        request_timeout_seconds=float(bridge_config["request_timeout_seconds"]),
    )
    bridge.start()

    print(f"Waiting for MT5 bridge on {bridge_config['host']}:{bridge_config['port']} ...")
    if not bridge.wait_for_client(timeout=120):
        raise SystemExit("MT5 bridge did not connect within 120 seconds")

    try:
        while True:
            try:
                run_once(bridge, storage, config)
            except BridgeError as exc:
                print(f"bridge error: {exc}")

            if args.run_once or config["scan"].get("run_once", False):
                break
            time.sleep(float(config["scan"]["interval_seconds"]))
    finally:
        bridge.stop()
        storage.close()


def _resolve_sqlite_path(config_path: str, sqlite_path: str) -> Path:
    path = Path(sqlite_path)
    if path.is_absolute():
        return path
    return Path(config_path).resolve().parent / path


def _print_result(result) -> None:
    spread_text = "n/a" if result.spread_to_atr is None else f"{result.spread_to_atr:.2%}"
    print(
        f"{result.symbol} {result.decision} score={result.score} "
        f"spread/ATR={spread_text} volume={result.suggested_volume} "
        f"margin={result.margin_required} reasons={result.reasons}"
    )


if __name__ == "__main__":
    main()

