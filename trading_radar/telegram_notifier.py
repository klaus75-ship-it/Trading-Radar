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
from .position_management import PositionManagementResult
from .prop_challenge import PropChallengeResult
from .structure_context import StructureContext


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool
    bot_token_env: str
    chat_id_env: str
    state_path: Path
    min_repeat_seconds: float
    notify_rejects: bool
    notify_observe_without_trigger: bool
    notify_pf_denied_setups: bool
    repeat_unchanged: bool

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
            notify_rejects=bool(raw.get("notify_rejects", False)),
            notify_observe_without_trigger=bool(raw.get("notify_observe_without_trigger", False)),
            notify_pf_denied_setups=bool(raw.get("notify_pf_denied_setups", False)),
            repeat_unchanged=bool(raw.get("repeat_unchanged", False)),
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
        management: PositionManagementResult | None = None,
    ) -> None:
        if not self.config.enabled:
            return

        if not self._should_notify(result, structure, prop, management):
            return

        fingerprint = self._fingerprint(result, structure, prop, session, management)
        now = time.time()
        last = self.state.get(result.symbol, {})
        last_sent_at = float(last.get("sent_at", 0))

        if fingerprint == last.get("fingerprint") and not self.config.repeat_unchanged:
            return
        if fingerprint == last.get("fingerprint") and now - last_sent_at < self.config.min_repeat_seconds:
            return

        message = self._format_message(result, structure, prop, session, context, management)
        if not self._send(message):
            return
        self.state[result.symbol] = {
            "fingerprint": fingerprint,
            "sent_at": now,
        }
        self._save_state()

    def _should_notify(
        self,
        result: TradeabilityResult,
        structure: MarketStructureResult | None,
        prop: PropChallengeResult | None,
        management: PositionManagementResult | None,
    ) -> bool:
        if management is not None:
            return True
        if result.decision == "REJECT":
            return self.config.notify_rejects
        if result.decision != "OBSERVE":
            return False
        if structure is None:
            return self.config.notify_observe_without_trigger
        if structure.trigger_level is None:
            return self.config.notify_observe_without_trigger
        if prop is not None and prop.status == "PF 暫不允許":
            return self.config.notify_pf_denied_setups
        return True

    def _fingerprint(
        self,
        result: TradeabilityResult,
        structure: MarketStructureResult | None,
        prop: PropChallengeResult | None,
        session: MarketSession | None,
        management: PositionManagementResult | None,
    ) -> str:
        reason_key = "|".join(_normalize_reason(reason) for reason in result.reasons)
        structure_key = ""
        if structure is not None:
            structure_key = (
                f"{structure.structure}:{structure.bias}:"
                f"{_confidence_bucket(structure.confidence)}:"
                f"{_signal_level_bucket(structure.trigger_level, result.atr)}:"
                f"{_signal_level_bucket(structure.invalid_level, result.atr)}"
            )
        prop_key = "" if prop is None else (
            f"{prop.status}:{prop.mode}:"
            f"{_signal_level_bucket(prop.stop_points, result.atr)}"
        )
        session_key = "" if session is None else session.status
        management_key = "" if management is None else (
            f"{management.stage}:{management.side}:{_signal_level_bucket(management.entry_price, result.atr)}:"
            f"{_signal_level_bucket(management.stop_reference, result.atr)}:"
            f"{_signal_level_bucket(management.target_reference, result.atr)}:"
            f"{_risk_note_key(management.risk_note)}"
        )
        return f"{result.decision}:{reason_key}:{structure_key}:{prop_key}:{session_key}:{management_key}"

    def _format_message(
        self,
        result: TradeabilityResult,
        structure: MarketStructureResult | None,
        prop: PropChallengeResult | None,
        session: MarketSession | None,
        context: StructureContext | None,
        management: PositionManagementResult | None,
    ) -> str:
        spread_text = _pct(result.spread_to_atr)
        reasons = _format_reasons(result.reasons)
        if management is not None:
            return _format_management_message(result, structure, prop, session, context, management)
        if structure is None:
            return (
                f"{result.symbol} | AVOID - 資料不足\n"
                f"{_format_session_line(session)}"
                f"分數: {result.score}/100\n"
                f"\n"
                f"成本: spread/ATR {spread_text}\n"
                f"雷達判斷:\n{reasons}"
            )

        category = _message_category(result, structure, prop, session, context)
        risk_line = _compact_risk_line(result, prop)
        return (
            f"{result.symbol} | {category} - {_category_title(category, structure)}\n"
            f"{_format_session_line(session)}"
            f"現在: {structure.structure} / {structure.bias} / 信心 {structure.confidence}/100\n"
            f"下一步: {_next_step(category, structure, context)}\n"
            f"觸發: {_level(structure.trigger_level)} | 失效: {_level(structure.invalid_level)} | 目標: {_level(structure.target_level)}\n"
            f"PF/風險: {_prop_summary(prop)} | {risk_line}\n"
            f"\n"
            f"Setup:\n"
            f"- {structure.setup}\n"
            f"{_format_prop_reasons(prop)}"
            f"{reasons}\n"
            f"\n"
            f"Report:\n"
            f"- 風控分: {result.score}/100 | spread/ATR: {spread_text}\n"
            f"{_format_context(context)}"
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
                state = json.load(file)
            if not isinstance(state, dict):
                raise ValueError(f"expected object, got {type(state).__name__}")
            return state
        except (JSONDecodeError, OSError, ValueError) as exc:
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


def _signal_level_bucket(value: float | None, atr: float | None) -> str:
    if value is None:
        return "none"
    width = max((atr or 0.0) * 0.25, 1.0)
    return str(round(value / width))


def _confidence_bucket(value: int) -> str:
    return str((value // 5) * 5)


def _risk_note_key(note: str) -> str:
    if "未偵測到 SL" in note:
        return "missing_sl"
    if "收緊" in note:
        return "tighten_sl"
    if "SL 已接近" in note:
        return "sl_ok"
    return note[:40]


def _message_category(
    result: TradeabilityResult,
    structure: MarketStructureResult,
    prop: PropChallengeResult | None,
    session: MarketSession | None,
    context: StructureContext | None,
) -> str:
    if session is not None and (session.is_weekend or session.is_stale):
        return "HEALTH"
    if result.decision != "OBSERVE":
        return "AVOID"
    if prop is not None and prop.status == "PF 暫不允許":
        return "AVOID"
    if structure.trigger_level is None:
        return "SETUP"
    if structure.structure in {"高位消化", "低位消化", "壓縮待突破"}:
        return "SETUP"
    if _is_chasing(structure, context):
        return "SETUP"
    if structure.confidence < 70:
        return "SETUP"
    return "ENTRY"


def _category_title(category: str, structure: MarketStructureResult) -> str:
    if category == "ENTRY":
        return "接近可執行，等觸發"
    if category == "AVOID":
        return "暫不交易"
    if category == "HEALTH":
        return "資料/盤別只供回顧"
    if structure.structure in {"高位消化", "低位消化"}:
        return "消化中，不追價"
    return "等條件成形"


def _next_step(
    category: str,
    structure: MarketStructureResult,
    context: StructureContext | None,
) -> str:
    if category == "ENTRY":
        return "只等價格觸發，不提前進"
    if category == "AVOID":
        return "等待更乾淨的位置，現在不進"
    if category == "HEALTH":
        return "只做結構回顧，不當即時訊號"
    if structure.trigger_level is None:
        return "先觀察，不設入場"
    if structure.structure in {"高位消化", "低位消化"}:
        return "等突破後接受或回測確認，不在箱體中段追"
    if _is_chasing(structure, context):
        return "離合理位置偏遠，等新回踩或新結構"
    return "等條件確認，還不是入場提醒"


def _is_chasing(structure: MarketStructureResult, context: StructureContext | None) -> bool:
    if context is None:
        return False
    if structure.bias == "偏多" and context.range_position is not None:
        return context.range_position > 0.80
    if structure.bias == "偏空" and context.range_position is not None:
        return context.range_position < 0.20
    return False


def _compact_risk_line(result: TradeabilityResult, prop: PropChallengeResult | None) -> str:
    pieces = [f"手數 {result.suggested_volume:g}", f"最小手風險 {_pct(result.min_volume_risk_fraction)}"]
    if prop is not None and prop.risk_one_contract is not None:
        pieces.append(f"PF 1口 {_money(prop.risk_one_contract)}")
    return " / ".join(pieces)


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
        return "等條件成形"
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


def _format_management_message(
    result: TradeabilityResult,
    structure: MarketStructureResult | None,
    prop: PropChallengeResult | None,
    session: MarketSession | None,
    context: StructureContext | None,
    management: PositionManagementResult,
) -> str:
    structure_line = "n/a" if structure is None else (
        f"{structure.structure} / {structure.bias} / 結構信心 {structure.confidence}/100"
    )
    return (
        f"{result.symbol} | 持倉管理\n"
        f"{_format_session_line(session)}"
        f"持倉: {management.side} {management.volume:g} lot @ {_money(management.entry_price)}\n"
        f"現價: {_money(management.current_price)} | 浮動: {management.unrealized_points:.2f} 點 / {_money(management.profit)}\n"
        f"階段: {_stage_text(management.stage)}\n"
        f"建議: {management.action}\n"
        f"\n"
        f"結構: {structure_line}\n"
        f"保護位: {_level(management.stop_reference)} | 目標參考: {_level(management.target_reference)}\n"
        f"風險: {management.risk_note}\n"
        f"PF: {_prop_summary(prop)}\n"
        f"\n"
        f"Report:\n"
        f"- 風控分: {result.score}/100\n"
        f"{_format_context(context)}\n"
        f"\n"
        f"理由:\n"
        f"{_format_management_reasons(management)}"
    )


def _format_management_reasons(management: PositionManagementResult) -> str:
    if not management.reasons:
        return "- 偵測到既有持倉，切換成管理模式"
    return "\n".join(f"- {reason}" for reason in management.reasons)


def _stage_text(stage: str) -> str:
    mapping = {
        "AGAINST_STRUCTURE": "方向相反，優先降風險",
        "INVALID": "結構失效",
        "PROTECT_PROFIT": "保護浮盈",
        "DEFEND": "防守",
        "BOX_MANAGE": "箱體內管理",
        "MANAGE": "持倉管理",
    }
    return mapping.get(stage, stage)


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
