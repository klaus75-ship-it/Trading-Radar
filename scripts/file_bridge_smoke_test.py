from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_radar.app_file import _freshness_age_seconds, _position_from_payload, run_once
from trading_radar.models import Bar
from trading_radar.storage import Storage
from trading_radar.telegram_notifier import TelegramConfig, TelegramNotifier


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_path = root / "state.json"
        db_path = root / "radar.sqlite3"
        config = _config(state_path, db_path)
        _write_state(state_path)

        storage = Storage(str(db_path))
        notifier = TelegramNotifier(TelegramConfig.from_config(config, root))
        try:
            run_once(config, storage, notifier)
            rows = storage.connection.execute("select count(*) from scans").fetchone()[0]
            assert rows == 2, rows

            stale_config = dict(config)
            stale_config["scan"] = dict(config["scan"], max_state_age_seconds=-1)
            run_once(stale_config, storage, notifier)
            health_rows = storage.connection.execute(
                "select count(*) from health_events where component='file_bridge'"
            ).fetchone()[0]
            assert health_rows >= 1, health_rows
        finally:
            storage.close()

    _assert_broker_time_age()
    _assert_zero_stop_is_missing()
    _assert_bad_telegram_state_recovers()
    print("file bridge smoke ok")


def _config(state_path: Path, db_path: Path) -> dict:
    return {
        "_config_path": str(state_path.parent / "config_file.json"),
        "account": {
            "risk_per_trade": 0.005,
            "max_margin_usage": 0.25,
            "allow_min_lot_when_over_budget": False,
            "max_min_lot_risk": 0.03,
        },
        "scan": {"interval_seconds": 60, "run_once": True, "max_state_age_seconds": 30},
        "storage": {"sqlite_path": str(db_path)},
        "telegram": {"enabled": False, "state_path": str(state_path.parent / "telegram_state.json")},
        "file_bridge": {
            "state_file": str(state_path),
            "timeframe": "M15",
            "read_attempts": 2,
            "read_retry_seconds": 0.01,
        },
        "symbols": {
            "XAUUSD": {
                "enabled": True,
                "atr_period": 14,
                "stop_atr_multiple": 1.2,
                "max_spread_to_atr": 0.12,
                "max_tick_age_seconds": 7200,
                "min_score": 70,
            },
            "NDX100": {
                "enabled": True,
                "atr_period": 14,
                "stop_atr_multiple": 1.2,
                "max_spread_to_atr": 0.15,
                "max_tick_age_seconds": 7200,
                "min_score": 70,
            },
        },
    }


def _write_state(path: Path) -> None:
    now = int(time.time())
    state = {
        "schema": 1,
        "ts": now,
        "server_time": now,
        "account": {"balance": 5000, "equity": 5000, "margin_free": 4800, "currency": "USD"},
        "positions": [
            {
                "ticket": 123,
                "symbol": "NDX100",
                "side": "BUY",
                "volume": 1.0,
                "entry_price": 19000,
                "current_price": 19020,
                "stop_loss": 0.0,
                "take_profit": 0.0,
                "profit": 20,
            }
        ],
        "symbols": [
            _symbol("XAUUSD", 2350.10, 2350.25, 0.01, now),
            _symbol("NDX100", 19000.0, 19003.0, 0.1, now),
        ],
    }
    path.write_text(json.dumps(state), encoding="utf-8")


def _symbol(symbol: str, bid: float, ask: float, point: float, now: int) -> dict:
    return {
        "ok": True,
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "spread": ask - bid,
        "tick_time": now,
        "point": point,
        "digits": 2,
        "volume_min": 0.01,
        "volume_step": 0.01,
        "volume_max": 100,
        "tick_value": 1.0,
        "tick_size": point,
        "contract_size": 100,
        "trade_stops_level": 50,
        "trade_freeze_level": 0,
        "swap_long": -10,
        "swap_short": 5,
        "trade_mode": 4,
        "margin_buy_min": 42.5,
        "bars": _bars(2350.0 if symbol == "XAUUSD" else 19000.0, now),
    }


def _bars(price: float, now: int) -> list[dict]:
    start = now - 90 * 900
    output = []
    for index in range(90):
        open_price = price + index * 0.2
        output.append(
            {
                "time": start + index * 900,
                "open": open_price,
                "high": open_price + 1.0,
                "low": open_price - 1.0,
                "close": open_price + 0.5,
                "tick_volume": 100,
                "spread": 0,
            }
        )
    return output


def _assert_broker_time_age() -> None:
    now = 100_000.0
    server_time = now + 10_800
    tick_time = server_time - 600
    age = _freshness_age_seconds(
        now_ts=now,
        tick_time=tick_time,
        state_mtime=now,
        server_time=server_time,
        bars=[Bar(time=int(server_time - 900), open=1, high=1, low=1, close=1)],
        max_future_skew_seconds=300,
    )
    assert 599 <= age <= 601, age


def _assert_zero_stop_is_missing() -> None:
    position = _position_from_payload(
        {
            "symbol": "XAUUSD",
            "side": "BUY",
            "volume": 1,
            "entry_price": 2350,
            "current_price": 2351,
            "profit": 1,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }
    )
    assert position.stop_loss is None
    assert position.take_profit is None


def _assert_bad_telegram_state_recovers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_path = Path(tmp) / "telegram_state.json"
        state_path.write_text("[]", encoding="utf-8")
        config = TelegramConfig.from_config(
            {"telegram": {"enabled": True, "state_path": str(state_path)}},
            Path(tmp),
        )
        notifier = TelegramNotifier(config)
        assert notifier.state == {}
        assert state_path.with_suffix(".json.bad").exists()


if __name__ == "__main__":
    main()
