from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import AccountInfo, Bar, OrderCheck, SymbolSnapshot


class WolveFileBridgeError(RuntimeError):
    pass


class WolveFileBridge:
    def __init__(self, common_files_dir: str, max_price_age_seconds: float = 120.0):
        self.common_files_dir = Path(common_files_dir)
        self.max_price_age_seconds = max_price_age_seconds

    def account(self, symbol_config: dict[str, Any]) -> AccountInfo:
        payload = self._read_json(symbol_config["stats_file"])
        return AccountInfo(
            balance=float(payload.get("balance", 0.0)),
            equity=float(payload.get("equity", 0.0)),
            margin_free=float(payload.get("equity", payload.get("balance", 0.0))),
            margin_level=None,
            currency="",
        )

    def snapshot(self, symbol: str, symbol_config: dict[str, Any]) -> SymbolSnapshot:
        payload = self._read_json(symbol_config["prices_file"])
        actual_symbol = str(payload.get("symbol", symbol))
        if actual_symbol != symbol:
            raise WolveFileBridgeError(
                f"{symbol_config['prices_file']} contains {actual_symbol}, expected {symbol}"
            )

        ts = _parse_wolve_time(str(payload.get("ts", "")))
        age = max(0.0, datetime.now(timezone.utc).timestamp() - ts.timestamp())
        if age > self.max_price_age_seconds:
            raise WolveFileBridgeError(
                f"{symbol} price file is stale: {age:.1f}s > {self.max_price_age_seconds:.1f}s"
            )

        bid = float(payload["bid"])
        ask = float(payload["ask"])
        point = float(symbol_config["point"])
        spread = ask - bid

        return SymbolSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            bid=bid,
            ask=ask,
            spread=spread,
            spread_points=spread / point if point > 0 else 0.0,
            tick_age_seconds=age,
            point=point,
            digits=int(symbol_config["digits"]),
            volume_min=float(symbol_config["volume_min"]),
            volume_step=float(symbol_config["volume_step"]),
            volume_max=float(symbol_config["volume_max"]),
            tick_value=float(symbol_config["tick_value"]),
            tick_size=float(symbol_config["tick_size"]),
            contract_size=float(symbol_config["contract_size"]),
            trade_stops_level=int(symbol_config["trade_stops_level"]),
            trade_freeze_level=int(symbol_config["trade_freeze_level"]),
            swap_long=float(symbol_config["swap_long"]),
            swap_short=float(symbol_config["swap_short"]),
            trade_mode=int(symbol_config["trade_mode"]),
        )

    def bars(self, symbol_config: dict[str, Any], count: int) -> list[Bar]:
        path = self.common_files_dir / symbol_config["history_file"]
        if not path.exists():
            raise WolveFileBridgeError(f"history file does not exist: {path}")

        rows: list[Bar] = []
        with open(path, "r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for row in reader:
                dt = _parse_wolve_time(row["time"])
                rows.append(
                    Bar(
                        time=int(dt.timestamp()),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        tick_volume=float(row.get("volume", 0.0)),
                        spread=0.0,
                    )
                )
        return rows[-count:]

    def order_check(self, snapshot: SymbolSnapshot, volume: float) -> OrderCheck:
        # WolveBridgeEA does not expose OrderCheck through files yet.
        return OrderCheck(ok=True, retcode=0, margin=None, comment="wolve file bridge: no order_check")

    def _read_json(self, file_name: str) -> dict[str, Any]:
        path = self.common_files_dir / file_name
        if not path.exists():
            raise WolveFileBridgeError(f"file does not exist: {path}")
        with open(path, "r", encoding="utf-8-sig") as file:
            return json.load(file)


def _parse_wolve_time(value: str) -> datetime:
    if not value:
        raise WolveFileBridgeError("missing timestamp")
    return datetime.strptime(value, "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)

