from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .market_structure import MarketStructureResult
from .models import AccountInfo, SymbolSnapshot, TradeabilityResult
from .prop_challenge import PropChallengeResult


class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def close(self) -> None:
        self.connection.close()

    def save_bridge_event(
        self,
        symbol: str,
        reason: str,
        account: AccountInfo | None = None,
        config_snapshot: dict | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO scans (
              timestamp, symbol, decision, score, reasons_json,
              account_equity, account_margin_free, account_margin_level,
              config_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                symbol,
                "REJECT",
                0,
                json.dumps([reason], ensure_ascii=False),
                account.equity if account is not None else None,
                account.margin_free if account is not None else None,
                account.margin_level if account is not None else None,
                json.dumps(config_snapshot, ensure_ascii=False, sort_keys=True) if config_snapshot is not None else None,
            ),
        )
        self.connection.commit()

    def save_scan(
        self,
        snapshot: SymbolSnapshot,
        result: TradeabilityResult,
        account: AccountInfo | None = None,
        structure: MarketStructureResult | None = None,
        prop: PropChallengeResult | None = None,
        config_snapshot: dict | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO scans (
              timestamp, symbol, bid, ask, spread, spread_points, tick_age_seconds,
              atr, spread_to_atr, stop_distance, suggested_volume, margin_required,
              decision, score, reasons_json, risk_budget, min_volume_loss,
              min_volume_risk_fraction, min_account_equity_required,
              account_equity, account_margin_free, account_margin_level,
              structure, structure_bias, structure_setup, structure_confidence,
              structure_trigger_level, structure_invalid_level, structure_target_level,
              structure_reasons_json, prop_status, prop_mode, prop_stop_points,
              prop_risk_one_contract, prop_risk_aggressive, prop_reasons_json,
              config_snapshot_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.timestamp.isoformat(),
                snapshot.symbol,
                snapshot.bid,
                snapshot.ask,
                snapshot.spread,
                snapshot.spread_points,
                snapshot.tick_age_seconds,
                result.atr,
                result.spread_to_atr,
                result.stop_distance,
                result.suggested_volume,
                result.margin_required,
                result.decision,
                result.score,
                json.dumps(result.reasons, ensure_ascii=False),
                result.risk_budget,
                result.min_volume_loss,
                result.min_volume_risk_fraction,
                result.min_account_equity_required,
                account.equity if account is not None else None,
                account.margin_free if account is not None else None,
                account.margin_level if account is not None else None,
                structure.structure if structure is not None else None,
                structure.bias if structure is not None else None,
                structure.setup if structure is not None else None,
                structure.confidence if structure is not None else None,
                structure.trigger_level if structure is not None else None,
                structure.invalid_level if structure is not None else None,
                structure.target_level if structure is not None else None,
                json.dumps(structure.reasons, ensure_ascii=False) if structure is not None else None,
                prop.status if prop is not None else None,
                prop.mode if prop is not None else None,
                prop.stop_points if prop is not None else None,
                prop.risk_one_contract if prop is not None else None,
                prop.risk_aggressive if prop is not None else None,
                json.dumps(prop.reasons, ensure_ascii=False) if prop is not None else None,
                json.dumps(config_snapshot, ensure_ascii=False, sort_keys=True) if config_snapshot is not None else None,
            ),
        )
        self.connection.commit()

    def _ensure_schema(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              timestamp TEXT NOT NULL,
              symbol TEXT NOT NULL,
              bid REAL,
              ask REAL,
              spread REAL,
              spread_points REAL,
              tick_age_seconds REAL,
              atr REAL,
              spread_to_atr REAL,
              stop_distance REAL,
              suggested_volume REAL,
              margin_required REAL,
              decision TEXT,
              score INTEGER,
              reasons_json TEXT,
              risk_budget REAL,
              min_volume_loss REAL,
              min_volume_risk_fraction REAL,
              min_account_equity_required REAL,
              account_equity REAL,
              account_margin_free REAL,
              account_margin_level REAL,
              structure TEXT,
              structure_bias TEXT,
              structure_setup TEXT,
              structure_confidence INTEGER,
              structure_trigger_level REAL,
              structure_invalid_level REAL,
              structure_target_level REAL,
              structure_reasons_json TEXT,
              prop_status TEXT,
              prop_mode TEXT,
              prop_stop_points REAL,
              prop_risk_one_contract REAL,
              prop_risk_aggressive REAL,
              prop_reasons_json TEXT,
              config_snapshot_json TEXT
            )
            """
        )
        self._ensure_column("scans", "risk_budget", "REAL")
        self._ensure_column("scans", "min_volume_loss", "REAL")
        self._ensure_column("scans", "min_volume_risk_fraction", "REAL")
        self._ensure_column("scans", "min_account_equity_required", "REAL")
        self._ensure_column("scans", "account_equity", "REAL")
        self._ensure_column("scans", "account_margin_free", "REAL")
        self._ensure_column("scans", "account_margin_level", "REAL")
        self._ensure_column("scans", "structure", "TEXT")
        self._ensure_column("scans", "structure_bias", "TEXT")
        self._ensure_column("scans", "structure_setup", "TEXT")
        self._ensure_column("scans", "structure_confidence", "INTEGER")
        self._ensure_column("scans", "structure_trigger_level", "REAL")
        self._ensure_column("scans", "structure_invalid_level", "REAL")
        self._ensure_column("scans", "structure_target_level", "REAL")
        self._ensure_column("scans", "structure_reasons_json", "TEXT")
        self._ensure_column("scans", "prop_status", "TEXT")
        self._ensure_column("scans", "prop_mode", "TEXT")
        self._ensure_column("scans", "prop_stop_points", "REAL")
        self._ensure_column("scans", "prop_risk_one_contract", "REAL")
        self._ensure_column("scans", "prop_risk_aggressive", "REAL")
        self._ensure_column("scans", "prop_reasons_json", "TEXT")
        self._ensure_column("scans", "config_snapshot_json", "TEXT")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, column_type: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row[1] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
