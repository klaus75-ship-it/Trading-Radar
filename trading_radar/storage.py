from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import SymbolSnapshot, TradeabilityResult


class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def close(self) -> None:
        self.connection.close()

    def save_scan(self, snapshot: SymbolSnapshot, result: TradeabilityResult) -> None:
        self.connection.execute(
            """
            INSERT INTO scans (
              timestamp, symbol, bid, ask, spread, spread_points, tick_age_seconds,
              atr, spread_to_atr, stop_distance, suggested_volume, margin_required,
              decision, score, reasons_json, risk_budget, min_volume_loss,
              min_volume_risk_fraction, min_account_equity_required
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
              min_account_equity_required REAL
            )
            """
        )
        self._ensure_column("scans", "risk_budget", "REAL")
        self._ensure_column("scans", "min_volume_loss", "REAL")
        self._ensure_column("scans", "min_volume_risk_fraction", "REAL")
        self._ensure_column("scans", "min_account_equity_required", "REAL")
        self.connection.commit()

    def _ensure_column(self, table: str, column: str, column_type: str) -> None:
        rows = self.connection.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row[1] == column for row in rows):
            return
        self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
