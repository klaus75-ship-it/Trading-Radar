from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .filters import evaluate_tradeability
from .metrics import atr_from_bars
from .storage import Storage
from .wolve_file_bridge import WolveFileBridge, WolveFileBridgeError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config_wolve.json")
    parser.add_argument("--run-once", action="store_true")
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as file:
        config = json.load(file)

    storage = Storage(str(_resolve_path(args.config, config["storage"]["sqlite_path"])))
    bridge = WolveFileBridge(
        common_files_dir=config["wolve"]["common_files_dir"],
        max_price_age_seconds=float(config["scan"]["max_price_age_seconds"]),
    )

    try:
        while True:
            run_once(bridge, storage, config)
            if args.run_once or config["scan"].get("run_once", False):
                break
            time.sleep(float(config["scan"]["interval_seconds"]))
    finally:
        storage.close()


def run_once(bridge: WolveFileBridge, storage: Storage, config: dict) -> None:
    for symbol, symbol_config in config["symbols"].items():
        if not symbol_config.get("enabled", True):
            continue

        try:
            account = bridge.account(symbol_config)
            snapshot = bridge.snapshot(symbol, symbol_config)
            bars = bridge.bars(symbol_config, count=max(200, int(symbol_config["atr_period"]) + 2))
            atr = atr_from_bars(bars, int(symbol_config["atr_period"]))
            order_check = bridge.order_check(snapshot, snapshot.volume_min)
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
        except WolveFileBridgeError as exc:
            print(f"{symbol} REJECT bridge_error={exc}")


def _resolve_path(config_path: str, target_path: str) -> Path:
    path = Path(target_path)
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

