from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .models import TradeabilityResult


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token_env: str
    chat_id_env: str
    state_path: Path
    min_repeat_seconds: float

    @classmethod
    def from_config(cls, config: dict, base_dir: Path) -> "TelegramConfig":
        raw = config.get("telegram", {})
        state_path = Path(raw.get("state_path", "telegram_state.json"))
        if not state_path.is_absolute():
            state_path = base_dir / state_path
        return cls(
            enabled=bool(raw.get("enabled", False)),
            bot_token_env=str(raw.get("bot_token_env", "TELEGRAM_BOT_TOKEN")),
            chat_id_env=str(raw.get("chat_id_env", "TELEGRAM_CHAT_ID")),
            state_path=state_path,
            min_repeat_seconds=float(raw.get("min_repeat_seconds", 3600)),
        )


class TelegramNotifier:
    def __init__(self, config: TelegramConfig):
        self.config = config
        self.state = self._load_state()

    def notify_result(self, result: TradeabilityResult) -> None:
        if not self.config.enabled:
            return

        fingerprint = self._fingerprint(result)
        now = time.time()
        last = self.state.get(result.symbol, {})
        last_sent_at = float(last.get("sent_at", 0))

        if fingerprint == last.get("fingerprint") and now - last_sent_at < self.config.min_repeat_seconds:
            return

        message = self._format_message(result)
        self._send(message)
        self.state[result.symbol] = {
            "fingerprint": fingerprint,
            "sent_at": now,
        }
        self._save_state()

    def _fingerprint(self, result: TradeabilityResult) -> str:
        reason_key = "|".join(result.reasons)
        risk_bucket = _bucket(result.min_volume_risk_fraction, 0.01)
        spread_bucket = _bucket(result.spread_to_atr, 0.005)
        return f"{result.decision}:{result.score}:{risk_bucket}:{spread_bucket}:{reason_key}"

    def _format_message(self, result: TradeabilityResult) -> str:
        spread_text = _pct(result.spread_to_atr)
        min_risk_text = _pct(result.min_volume_risk_fraction)
        target_equity = "n/a" if result.min_account_equity_required is None else f"{result.min_account_equity_required:.2f}"
        volume = f"{result.suggested_volume:g}"
        margin = "n/a" if result.margin_required is None else f"{result.margin_required:.2f}"
        reasons = "\n".join(f"- {reason}" for reason in result.reasons) if result.reasons else "- none"
        return (
            f"{result.symbol} {result.decision}\n"
            f"score: {result.score}\n"
            f"spread/ATR: {spread_text}\n"
            f"suggested volume: {volume}\n"
            f"min lot risk: {min_risk_text}\n"
            f"target equity: {target_equity}\n"
            f"min margin: {margin}\n"
            f"reasons:\n{reasons}"
        )

    def _send(self, message: str) -> None:
        token = os.environ.get(self.config.bot_token_env, "")
        chat_id = os.environ.get(self.config.chat_id_env, "")
        if not token or not chat_id:
            print(
                f"telegram disabled: missing ${self.config.bot_token_env} or ${self.config.chat_id_env}"
            )
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()

    def _load_state(self) -> dict:
        if not self.config.state_path.exists():
            return {}
        with open(self.config.state_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _save_state(self) -> None:
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config.state_path, "w", encoding="utf-8") as file:
            json.dump(self.state, file, indent=2, sort_keys=True)


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _bucket(value: float | None, width: float) -> str:
    if value is None:
        return "none"
    if width <= 0:
        return f"{value:.6f}"
    return str(round(value / width))

