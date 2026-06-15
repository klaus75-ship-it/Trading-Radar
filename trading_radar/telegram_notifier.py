from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from urllib.error import HTTPError, URLError

from .market_structure import MarketStructureResult
from .market_session import MarketSession
from .models import TradeabilityResult
from .prop_challenge import PropChallengeResult
from .structure_context import StructureContext


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
        self._last_error_at = 0.0

    def notify_result(
        self,
        result: TradeabilityResult,
        structure: MarketStructureResult | None = None,
        prop: PropChallengeResult | None = None,
        session: MarketSession | None = None,
        context: StructureContext | None = None,
    ) -> None:
        if not self.config.enabled:
            return

        fingerprint = self._fingerprint(result, structure, prop, session)
        now = time.time()
        last = self.state.get(result.symbol, {})
        last_sent_at = float(last.get("sent_at", 0))

        if fingerprint == last.get("fingerprint") and now - last_sent_at < self.config.min_repeat_seconds:
            return

        message = self._format_message(result, structure, prop, session, context)
        if not self._send(message):
            return
        self.state[result.symbol] = {
            "fingerprint": fingerprint,
            "sent_at": now,
        }
        self._save_state()

    def _fingerprint(
        self,
        result: TradeabilityResult,
        structure: MarketStructureResult | None,
        prop: PropChallengeResult | None,
        session: MarketSession | None,
    ) -> str:
        reason_key = "|".join(_normalize_reason(reason) for reason in result.reasons)
        risk_bucket = _bucket(result.min_volume_risk_fraction, 0.01)
        spread_bucket = _bucket(result.spread_to_atr, 0.005)
        structure_key = ""
        if structure is not None:
            structure_key = (
                f"{structure.structure}:{structure.bias}:{structure.confidence}:"
                f"{_level_bucket(structure.trigger_level)}:"
                f"{_level_bucket(structure.invalid_level)}"
            )
        prop_key = "" if prop is None else (
            f"{prop.status}:{_level_bucket(prop.stop_points)}:"
            f"{_level_bucket(prop.risk_one_contract)}:{_level_bucket(prop.risk_aggressive)}"
        )
        session_key = "" if session is None else session.status
        return f"{result.decision}:{result.score}:{risk_bucket}:{spread_bucket}:{reason_key}:{structure_key}:{prop_key}:{session_key}"

    def _format_message(
        self,
        result: TradeabilityResult,
        structure: MarketStructureResult | None,
        prop: PropChallengeResult | None,
        session: MarketSession | None,
        context: StructureContext | None,
    ) -> str:
        spread_text = _pct(result.spread_to_atr)
        min_risk_text = _pct(result.min_volume_risk_fraction)
        target_equity = _money(result.min_account_equity_required)
        volume = f"{result.suggested_volume:g}"
        margin = _money(result.margin_required)
        reasons = _format_reasons(result.reasons)
        decision = _decision_text(result, structure, prop)
        if structure is None:
            return (
                f"{result.symbol} | {decision}\n"
                f"{_format_session_line(session)}"
                f"分數: {result.score}/100\n"
                f"\n"
                f"成本: spread/ATR {spread_text}\n"
                f"建議手數: {volume} lot\n"
                f"最小手風險: {min_risk_text}\n"
                f"合理資金門檻: {target_equity}\n"
                f"預估保證金: {margin}\n"
                f"\n"
                f"雷達判斷:\n{reasons}"
            )

        return (
            f"{result.symbol} | {decision}\n"
            f"{_format_session_line(session)}"
            f"Summary: {structure.structure} / {structure.bias} / 結構信心 {structure.confidence}/100\n"
            f"Setup: {structure.setup}\n"
            f"PF: {_prop_summary(prop)}\n"
            f"建議手數: {volume} lot | 最小手風險: {min_risk_text}\n"
            f"觸發: {_level(structure.trigger_level)} | 失效: {_level(structure.invalid_level)} | 目標: {_level(structure.target_level)}\n"
            f"\n"
            f"風險:\n"
            f"- spread/ATR: {spread_text}\n"
            f"- 合理資金門檻: {target_equity}\n"
            f"- 預估保證金: {margin}\n"
            f"{_format_prop_compact(prop)}"
            f"\n"
            f"Report:\n"
            f"- 風控分: {result.score}/100\n"
            f"{_format_context(context)}\n"
            f"\n"
            f"理由:\n"
            f"{_format_structure_reasons(structure.reasons)}\n"
            f"{_format_prop_reasons(prop)}"
            f"{reasons}"
        )

    def _send(self, message: str) -> bool:
        token = os.environ.get(self.config.bot_token_env, "")
        chat_id = os.environ.get(self.config.chat_id_env, "")
        if not token or not chat_id:
            print(
                f"telegram disabled: missing ${self.config.bot_token_env} or ${self.config.chat_id_env}"
            )
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                response.read()
            return True
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            now = time.time()
            if now - self._last_error_at >= 60:
                print(f"telegram send failed: {exc}")
                self._last_error_at = now
            return False

    def _load_state(self) -> dict:
        if not self.config.state_path.exists():
            return {}
        try:
            with open(self.config.state_path, "r", encoding="utf-8") as file:
                return json.load(file)
        except (JSONDecodeError, OSError) as exc:
            bad_path = self.config.state_path.with_suffix(self.config.state_path.suffix + ".bad")
            try:
                self.config.state_path.replace(bad_path)
                print(f"telegram state file was invalid; moved to {bad_path}: {exc}")
            except OSError:
                print(f"telegram state file was invalid and could not be moved: {exc}")
            return {}

    def _save_state(self) -> None:
        self.config.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config.state_path.with_suffix(self.config.state_path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as file:
            json.dump(self.state, file, indent=2, sort_keys=True)
        tmp_path.replace(self.config.state_path)


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2%}"


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _level(value: float | None) -> str:
    if value is None:
        return "等待確認"
    return f"{value:.2f}"


def _level_bucket(value: float | None) -> str:
    if value is None:
        return "none"
    return f"{value:.1f}"


def _decision_text(
    result: TradeabilityResult,
    structure: MarketStructureResult | None,
    prop: PropChallengeResult | None,
) -> str:
    if result.decision == "REJECT":
        return "暫不交易"
    if result.decision == "OBSERVE":
        if structure is not None and structure.trigger_level is None:
            return "只觀察"
        if prop is not None and prop.status == "PF 暫不允許":
            return "只觀察"
        return "可操作觀察"
    return result.decision


def _format_reasons(reasons: list[str]) -> str:
    if not reasons:
        return "- 無主要過濾問題"
    return "\n".join(f"- {_translate_reason(reason)}" for reason in reasons)


def _format_session_line(session: MarketSession | None) -> str:
    if session is None:
        return ""
    return f"市場狀態: {session.status}，{session.note}\n"


def _format_context(context: StructureContext | None) -> str:
    if context is None:
        return "- n/a"
    return (
        f"- ATR: {_money(context.atr)}\n"
        f"- 近24根高低: {_money(context.recent_high)} / {_money(context.recent_low)}\n"
        f"- 目前位置: {_pct(context.range_position)}\n"
        f"- 區間寬度: {_money(context.range_width)} ({_ratio(context.range_atr)} ATR)\n"
        f"- 壓縮程度: {_pct(context.compression)}\n"
        f"- 最新K range: {_ratio(context.latest_range_atr)} ATR"
    )


def _prop_summary(prop: PropChallengeResult | None) -> str:
    if prop is None:
        return "n/a"
    return f"{prop.status} / {prop.mode}"


def _format_prop_compact(prop: PropChallengeResult | None) -> str:
    if prop is None:
        return ""
    return (
        f"- PF停損距離: {_level(prop.stop_points)} 點\n"
        f"- PF 1口/2口風險: {_money(prop.risk_one_contract)} / {_money(prop.risk_aggressive)}\n"
    )


def _format_prop_reasons(prop: PropChallengeResult | None) -> str:
    if prop is None or not prop.reasons:
        return ""
    compact = "；".join(prop.reasons[:3])
    more = "" if len(prop.reasons) <= 3 else f"；另 {len(prop.reasons) - 3} 項"
    return f"- PF: {compact}{more}\n"


def _ratio(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def _normalize_reason(reason: str) -> str:
    if reason.startswith("stale tick:"):
        return "stale tick"
    if reason.startswith("spread/ATR too high:"):
        return "spread/ATR too high"
    if reason.startswith("minimum lot risk"):
        return "minimum lot risk exceeds target"
    if reason.startswith("order check failed:"):
        return "order check failed"
    if reason.startswith("margin too high:"):
        return "margin too high"
    return reason


def _format_structure_reasons(reasons: list[str]) -> str:
    if not reasons:
        return "- 無明顯結構理由"
    return "\n".join(f"- {reason}" for reason in reasons)


def _format_prop_section(prop: PropChallengeResult | None) -> str:
    if prop is None:
        return ""
    stop = _level(prop.stop_points)
    risk_one = _money(prop.risk_one_contract)
    risk_aggressive = _money(prop.risk_aggressive)
    reasons = "\n".join(f"- {reason}" for reason in prop.reasons) if prop.reasons else "- 無"
    return (
        f"\n"
        f"\n"
        f"PF 模式:\n"
        f"{prop.status}\n"
        f"建議: {prop.mode}\n"
        f"停損距離: {stop} 點\n"
        f"{prop.one_contracts} 口風險: {risk_one}\n"
        f"{prop.aggressive_contracts} 口風險: {risk_aggressive}\n"
        f"條件:\n{reasons}\n"
    )


def _translate_reason(reason: str) -> str:
    if reason == "ATR unavailable":
        return "ATR 資料不足，先不判斷"
    if reason.startswith("stale tick:"):
        return "報價太久沒有更新"
    if reason.startswith("spread/ATR too high:"):
        value = reason.split(":", 1)[1].strip()
        return f"交易成本偏高，spread/ATR {value}"
    if reason == "stop distance below broker stops level":
        return "停損距離小於券商最低限制"
    if reason.startswith("minimum lot risk"):
        return "最小手數風險超過本次風控預算"
    if reason == "using minimum lot because it is within hard risk cap":
        return "使用最小手數，仍在硬性風險上限內"
    if reason.startswith("order check failed:"):
        return f"下單檢查未通過: {reason.split(':', 1)[1].strip()}"
    if reason.startswith("margin too high:"):
        return f"保證金需求偏高: {reason.split(':', 1)[1].strip()}"
    return reason


def _bucket(value: float | None, width: float) -> str:
    if value is None:
        return "none"
    if width <= 0:
        return f"{value:.6f}"
    return str(round(value / width))
