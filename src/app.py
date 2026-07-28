"""CLI and production ReAct loop for the persistent-memory gift advisor."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv


SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from memory import SQLiteMemory
from prompts import (
    CHATBOT_BASELINE_PROMPT,
    MAX_ITERATIONS,
    MAX_REPEATED_ACTIONS,
    MEMORY_MESSAGE_LIMIT,
    REACT_SYSTEM_PROMPT,
    TOOL_TIMEOUT_SECONDS,
)
from providers import LLMProvider, ProviderError, get_llm_provider
from tools import AVAILABLE_TOOLS, GIFT_CATALOG, PERSONALITY_KEYWORDS


load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class ParsedAction:
    """One validated action emitted by the LLM."""

    name: str
    args: tuple[Any, ...]
    raw: str


@dataclass
class AgentResult:
    """Structured result for CLI rendering, tests, and trace evaluation."""

    answer: str
    trace: list[str] = field(default_factory=list)
    tool_calls: list[ParsedAction] = field(default_factory=list)
    iterations: int = 0
    guardrail_triggered: bool = False


class _TraceRecorder(list[str]):
    """List-like trace store that can notify a UI stream as lines arrive."""

    def __init__(self, callback: Callable[[str], None] | None = None):
        super().__init__()
        self._callback = callback

    def append(self, item: str) -> None:
        super().append(item)
        if self._callback:
            self._callback(item)

    def extend(self, items) -> None:
        for item in items:
            self.append(item)


ACTION_PATTERN = re.compile(
    r"(?m)^\s*Action:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\[(.*)\]\s*$"
)
FINAL_PATTERN = re.compile(r"Final Answer:\s*(.+)", re.IGNORECASE | re.DOTALL)
CATEGORY_PATTERN = re.compile(r"Nhóm tính cách:\s*(Tri thức|Công nghệ|Thể thao)")
GIFT_PATTERN = re.compile(r"^'([^']+)'.*:\s*CÒN HÀNG", re.DOTALL)
SEARCH_GIFT_PATTERN = re.compile(r"(?m)^\d+\.\s+(.+?)\s+-\s+[\d,]+\s+VNĐ$")


def _fold_text(value: str) -> str:
    """Lowercase Vietnamese text into ASCII-ish tokens for stable intent checks."""
    text = str(value).lower().replace("đ", "d")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    )


def load_test_cases() -> list[dict[str, Any]]:
    """Load evaluation prompts from the project config."""
    with (PROJECT_ROOT / "config" / "test_cases.json").open(encoding="utf-8") as file:
        return json.load(file)


def _clean_llm_output(output: str) -> str:
    cleaned = output.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


def parse_action(output: str) -> ParsedAction | None:
    """Parse the strict ``Action: tool[arg, ...]`` protocol safely."""
    matches = ACTION_PATTERN.findall(output)
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("Mỗi lượt chỉ được tạo đúng một Action.")

    name, args_source = matches[0]
    try:
        args = ast.literal_eval(f"[{args_source}]")
    except (SyntaxError, ValueError) as exc:
        raise ValueError(
            "Action sai cú pháp. Dùng dạng tool_name[\"text\", 500000]."
        ) from exc
    if not isinstance(args, list):
        raise ValueError("Danh sách tham số Action không hợp lệ.")
    raw = f"{name}[{args_source}]"
    return ParsedAction(name=name, args=tuple(args), raw=raw)


def parse_final_answer(output: str) -> str | None:
    """Extract a final answer without requiring an exact line position."""
    match = FINAL_PATTERN.search(output)
    return match.group(1).strip() if match else None


def run_baseline_chatbot(user_query: str, provider: LLMProvider) -> str:
    """Run the one-call, no-tool baseline required by the rubric."""
    try:
        return provider.generate(user_query, system_prompt=CHATBOT_BASELINE_PROMPT)
    except ProviderError as exc:
        return f"Không thể gọi Chatbot baseline: {exc}"


class GiftAdvisorAgent:
    """A bounded ReAct agent with tool grounding and local SQLite memory."""

    def __init__(
        self,
        provider: LLMProvider,
        memory: SQLiteMemory | None = None,
        *,
        max_iterations: int = MAX_ITERATIONS,
        tool_timeout_seconds: int = TOOL_TIMEOUT_SECONDS,
    ):
        self.provider = provider
        self.memory = memory or SQLiteMemory()
        self.max_iterations = max(1, int(max_iterations))
        self.tool_timeout_seconds = max(1, int(tool_timeout_seconds))

    @staticmethod
    def _trace_from_output(output: str) -> list[str]:
        return [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith(("Thought:", "Action:", "Final Answer:"))
        ]

    @staticmethod
    def _normalized_action_key(action: ParsedAction) -> tuple[str, str]:
        return action.name, json.dumps(action.args, ensure_ascii=False, default=str)

    def _execute_tool(self, action: ParsedAction) -> str:
        tool = AVAILABLE_TOOLS.get(action.name)
        if tool is None:
            return (
                f"LỖI: Tool '{action.name}' không tồn tại. "
                f"Tools hợp lệ: {', '.join(AVAILABLE_TOOLS)}."
            )
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(tool, *action.args)
                result = future.result(timeout=self.tool_timeout_seconds)
            return str(result)
        except FutureTimeoutError:
            return (
                f"LỖI: Tool '{action.name}' vượt timeout "
                f"{self.tool_timeout_seconds} giây."
            )
        except TypeError as exc:
            return f"LỖI: Tham số cho tool '{action.name}' không hợp lệ: {exc}"
        except Exception as exc:
            return f"LỖI: Tool '{action.name}' gặp {type(exc).__name__}: {exc}"

    def _save_grounded_profile(
        self,
        session_id: str,
        user_query: str,
        action: ParsedAction,
        observation: str,
    ) -> None:
        if observation.startswith("LỖI:"):
            return
        if action.name == "analyze_personality":
            category_match = CATEGORY_PATTERN.search(observation)
            if category_match:
                self.memory.update_profile(
                    session_id,
                    traits=str(action.args[0]) if action.args else user_query,
                    category=category_match.group(1),
                )
        elif action.name == "search_gifts" and len(action.args) >= 2:
            try:
                budget = int(
                    str(action.args[1]).replace(".", "").replace(",", "").strip()
                )
            except ValueError:
                budget = None
            self.memory.update_profile(
                session_id,
                category=str(action.args[0]),
                budget_vnd=budget,
            )
        elif action.name == "check_gift_stock":
            gift_match = GIFT_PATTERN.search(observation)
            if gift_match:
                self.memory.update_profile(
                    session_id,
                    last_gift=gift_match.group(1),
                )

    @staticmethod
    def _extract_budget(user_query: str) -> int | str | None:
        text = _fold_text(user_query)
        million_match = re.search(r"(\d+(?:[,.]\d+)?)\s*trieu", text)
        if million_match:
            return int(float(million_match.group(1).replace(",", ".")) * 1_000_000)
        money_matches = re.findall(
            r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{5,})(?:\s*(?:vnd|vn d|d))?",
            text,
        )
        if money_matches:
            return int(money_matches[-1].replace(".", "").replace(",", ""))
        if "ngan sach" in text and (
            "rat nhieu" in text or "nhieu tien" in text or "khong gioi han" in text
        ):
            return "invalid_non_numeric_budget"
        return None

    @staticmethod
    def _explicit_category(user_query: str) -> str | None:
        text = _fold_text(user_query)
        return next(
            (category for category in GIFT_CATALOG if _fold_text(category) in text),
            None,
        )

    @staticmethod
    def _category_from_observations(
        observations: list[tuple[ParsedAction, str]],
    ) -> str | None:
        for action, observation in reversed(observations):
            if action.name == "analyze_personality":
                match = CATEGORY_PATTERN.search(observation)
                if match:
                    return match.group(1)
            if action.name == "search_gifts" and action.args:
                return str(action.args[0])
        return None

    @staticmethod
    def _gifts_from_observations(
        observations: list[tuple[ParsedAction, str]],
    ) -> list[str]:
        for action, observation in reversed(observations):
            if action.name == "search_gifts":
                return SEARCH_GIFT_PATTERN.findall(observation)
        return []

    @staticmethod
    def _memory_budget_reuse_requested(user_query: str) -> bool:
        text = _fold_text(user_query)
        markers = (
            "dung ngan sach cu",
            "ngan sach cu",
            "ngan sach lan truoc",
            "nhu lan truoc",
            "nhu cu",
            "lan truoc",
            "da luu",
            "previous budget",
            "same budget",
            "old budget",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _has_personality_signal(user_query: str) -> bool:
        text = _fold_text(user_query)
        return any(
            _fold_text(keyword) in text
            for keywords in PERSONALITY_KEYWORDS.values()
            for keyword in keywords
        )

    def _next_safe_action(
        self,
        user_query: str,
        profile: dict[str, Any],
        observations: list[tuple[ParsedAction, str]],
    ) -> ParsedAction | None:
        """Recover a rubric-compliant tool path when the model breaks protocol."""
        executed_names = [action.name for action, _ in observations]
        text = _fold_text(user_query)
        explicit_budget = self._extract_budget(user_query)
        reuse_memory_budget = self._memory_budget_reuse_requested(user_query)
        if "tai sao" in text and explicit_budget is None:
            return None
        category = (
            self._category_from_observations(observations)
            or self._explicit_category(user_query)
            or (
                profile.get("category")
                if explicit_budget is not None or reuse_memory_budget
                else None
            )
        )

        has_personality_signal = self._has_personality_signal(user_query)
        asks_for_analysis = "phan tich" in text or "tinh cach" in text
        if (
            not category
            and "analyze_personality" not in executed_names
            and (has_personality_signal or asks_for_analysis)
        ):
            return ParsedAction(
                name="analyze_personality",
                args=(user_query,),
                raw=f"analyze_personality[{user_query!r}]",
            )

        budget = explicit_budget
        if budget is None and reuse_memory_budget:
            budget = profile.get("budget_vnd")
        if category and budget is not None and "search_gifts" not in executed_names:
            return ParsedAction(
                name="search_gifts",
                args=(category, budget),
                raw=f"search_gifts[{category!r}, {budget!r}]",
            )

        gifts = self._gifts_from_observations(observations)
        if not gifts:
            return None

        checked = [
            str(action.args[0])
            for action, _ in observations
            if action.name == "check_gift_stock" and action.args
        ]
        available_checked = any(
            action.name == "check_gift_stock" and "con hang" in _fold_text(observation)
            for action, observation in observations
        )
        if available_checked:
            return None

        # Prefer a gift explicitly requested by the user, otherwise the last
        # (highest-priced) catalog result that is still within budget.
        explicit_gift = next(
            (gift for gift in gifts if _fold_text(gift) in text and gift not in checked),
            None,
        )
        candidate = explicit_gift or next(
            (gift for gift in reversed(gifts) if gift not in checked),
            None,
        )
        if candidate:
            return ParsedAction(
                name="check_gift_stock",
                args=(candidate,),
                raw=f"check_gift_stock[{candidate!r}]",
            )
        return None

    @staticmethod
    def _action_matches_expected(
        action: ParsedAction,
        expected: ParsedAction,
    ) -> bool:
        if action.name != expected.name:
            return False
        if action.name == "analyze_personality":
            return len(action.args) == 1
        return action.args == expected.args

    @staticmethod
    def _terminal_error_answer(
        observations: list[tuple[ParsedAction, str]],
    ) -> str | None:
        """Convert a terminal business error into a grounded safe response."""
        for action, observation in reversed(observations):
            if action.name == "analyze_personality" and observation.startswith("LỖI:"):
                return (
                    f"{observation}\n\n"
                    "Mình chưa thể suy ra một nhóm khác ngoài dữ liệu tool. "
                    "Bạn có thể mô tả thêm sở thích thuộc Tri thức, Công nghệ "
                    "hoặc Thể thao để mình tiếp tục."
                )
            if action.name == "search_gifts" and observation.startswith("LỖI:"):
                return (
                    f"{observation}\n\n"
                    "Vui lòng nhập ngân sách cụ thể bằng số VNĐ, ví dụ 500000."
                )
            if action.name == "search_gifts" and observation.startswith(
                "Không có quà"
            ):
                return (
                    f"{observation}\n\n"
                    "Bạn muốn tăng ngân sách đến mức giá tối thiểu trên hay đổi "
                    "sang nhóm quà khác?"
                )
            if action.name == "check_gift_stock" and observation.startswith("LỖI:"):
                return f"{observation}\n\nVui lòng chọn một tên quà có trong danh mục."
        return None

    @staticmethod
    def _catalog_names(category_fold: str | None = None) -> list[str]:
        names: list[str] = []
        for category, gifts in GIFT_CATALOG.items():
            if category_fold is not None and _fold_text(category) != category_fold:
                continue
            names.extend(name for name, _, _ in gifts)
        return names

    @classmethod
    def _mentions_out_of_catalog_product(cls, user_query: str) -> bool:
        text = _fold_text(user_query)
        if any(_fold_text(name) in text for name in cls._catalog_names()):
            return False
        unsupported_terms = (
            "pc",
            "laptop",
            "may tram",
            "workstation",
            "dien thoai",
            "tablet",
            "flagship",
            "smart home",
            "camera",
            "quay phim",
            "startup",
            "xe dien",
            "gaming setup",
            "ai tools",
            "may tinh ai",
        )
        return any(term in text for term in unsupported_terms)

    @staticmethod
    def _has_successful_search(
        observations: list[tuple[ParsedAction, str]],
    ) -> bool:
        return any(
            action.name == "search_gifts" and not observation.startswith("LỖI:")
            for action, observation in observations
        )

    @staticmethod
    def _grounded_stock_answer(
        observations: list[tuple[ParsedAction, str]],
    ) -> str | None:
        for action, observation in reversed(observations):
            if action.name != "check_gift_stock":
                continue
            if "con hang" not in _fold_text(observation):
                continue
            gift_name = str(action.args[0]) if action.args else "món quà này"
            match = re.match(r"^'([^']+)'", observation)
            if match:
                gift_name = match.group(1)
            return (
                f"Mình đề xuất '{gift_name}'.\n\n"
                f"Tool local đã xác nhận: {observation}\n\n"
                "Mình chỉ tư vấn dựa trên danh mục local, không tạo đơn hàng hoặc thanh toán."
            )
        return None

    def _grounding_guard_answer(
        self,
        user_query: str,
        proposed_answer: str,
        profile: dict[str, Any],
        observations: list[tuple[ParsedAction, str]],
    ) -> str | None:
        """Block answers that infer budget or product inventory without tools."""
        budget = self._extract_budget(user_query)
        reuse_memory_budget = self._memory_budget_reuse_requested(user_query)
        has_category_signal = bool(
            self._explicit_category(user_query)
            or self._has_personality_signal(user_query)
            or (reuse_memory_budget and profile.get("category"))
        )

        if self._mentions_out_of_catalog_product(user_query):
            tech_names = "; ".join(self._catalog_names("cong nghe"))
            return (
                "Mình không có PC/laptop/flagship trong danh mục local của bài lab, "
                "nên không thể lập danh sách hoặc tư vấn các sản phẩm đó.\n\n"
                f"Nhóm Công nghệ trong DB hiện chỉ có: {tech_names}.\n\n"
                "Nếu bạn muốn mình chọn quà trong DB, hãy nhập ngân sách cụ thể bằng số VNĐ "
                "(ví dụ 500000 hoặc 1 triệu)."
            )

        if isinstance(budget, str):
            return (
                "Ngân sách cần là số VNĐ cụ thể, ví dụ 500000 hoặc 1 triệu. "
                "Mình không quy đổi các mô tả như 'rất nhiều' thành ngân sách để tránh "
                "đề xuất sai danh mục."
            )

        if has_category_signal and budget is None and not reuse_memory_budget:
            remembered_budget = profile.get("budget_vnd")
            memory_note = (
                f"\n\nMình có thấy memory cũ {remembered_budget:,} VNĐ, nhưng đây là "
                "yêu cầu mới nên mình sẽ không tự dùng lại nếu bạn chưa nói rõ."
                if isinstance(remembered_budget, int)
                else ""
            )
            return (
                "Mình chưa có ngân sách cụ thể cho yêu cầu mới này. "
                "Vui lòng nhập ngân sách bằng số VNĐ, ví dụ 500000 hoặc 1 triệu, "
                "để mình tra đúng danh mục local trước khi đề xuất."
                f"{memory_note}"
            )

        answer_text = _fold_text(proposed_answer)
        recommendation_markers = (
            "de xuat",
            "goi y",
            "nen chon",
            "nen mua",
            "san pham",
            "mon qua",
            "pc",
            "laptop",
            "flagship",
        )
        if (
            has_category_signal
            and not self._has_successful_search(observations)
            and any(marker in answer_text for marker in recommendation_markers)
        ):
            return (
                "Mình chưa thể đề xuất sản phẩm khi chưa tra danh mục local bằng tool. "
                "Hãy nhập đủ sở thích và ngân sách số VNĐ; mình sẽ chạy đúng luồng "
                "analyze_personality -> search_gifts -> check_gift_stock."
            )

        return None

    def _return_guardrail_answer(
        self,
        session_id: str,
        answer: str,
        trace: list[str],
        tool_calls: list[ParsedAction],
        iteration: int,
        reason: str,
    ) -> AgentResult:
        trace.append(f"Guardrail: {reason}")
        trace.append(f"Final Answer: {answer}")
        self.memory.add_message(
            session_id,
            "assistant",
            answer,
            {
                "guardrail": True,
                "iterations": iteration,
                "tool_calls": [item.raw for item in tool_calls],
            },
        )
        return AgentResult(
            answer=answer,
            trace=trace,
            tool_calls=tool_calls,
            iterations=iteration,
            guardrail_triggered=True,
        )

    def _return_grounded_answer(
        self,
        session_id: str,
        answer: str,
        trace: list[str],
        tool_calls: list[ParsedAction],
        iteration: int,
    ) -> AgentResult:
        trace.append("Observation: Final answer được chuẩn hóa từ check_gift_stock.")
        trace.append(f"Final Answer: {answer}")
        self.memory.add_message(
            session_id,
            "assistant",
            answer,
            {
                "iterations": iteration,
                "tool_calls": [item.raw for item in tool_calls],
                "grounded_final": True,
            },
        )
        return AgentResult(
            answer=answer,
            trace=trace,
            tool_calls=tool_calls,
            iterations=iteration,
        )

    @staticmethod
    def _enforce_advisory_scope(answer: str) -> str:
        """Prevent the text-only agent from implying a purchase side effect."""
        replacements = {
            "chốt mua": "chốt phương án tư vấn",
            "chốt đơn": "chốt phương án tư vấn",
            "đặt mua": "đề xuất mua",
            "thanh toán": "hướng dẫn thanh toán",
        }
        normalized = answer
        changed = False
        for phrase, replacement in replacements.items():
            updated = re.sub(phrase, replacement, normalized, flags=re.IGNORECASE)
            changed = changed or updated != normalized
            normalized = updated
        if changed:
            normalized += (
                "\n\nLưu ý: Mình chỉ tư vấn, không thể tạo đơn hàng hoặc thực "
                "hiện thanh toán."
            )
        return normalized

    @staticmethod
    def _build_turn_prompt(
        user_query: str,
        memory_context: str,
        scratchpad: list[str],
    ) -> str:
        trace = "\n".join(scratchpad) if scratchpad else "(chưa có Action)"
        return f"""MEMORY CỦA SESSION
<memory>
{memory_context}
</memory>

YÊU CẦU HIỆN TẠI
{user_query}

TRACE ĐÃ ĐƯỢC ỨNG DỤNG XÁC MINH
{trace}

Hãy tạo đúng bước kế tiếp theo định dạng trong system prompt."""

    def _safe_fallback(self, scratchpad: list[str]) -> str:
        if scratchpad:
            return (
                "Mình chưa thể hoàn tất tư vấn trong giới hạn an toàn. "
                "Các bước đã xác minh được lưu trong trace; bạn có thể bổ sung "
                "mô tả sở thích và ngân sách bằng số VNĐ để thử lại."
            )
        return (
            "Mình chưa thể xử lý yêu cầu lúc này. Vui lòng thử lại với mô tả "
            "sở thích và ngân sách cụ thể bằng VNĐ."
        )

    def run(
        self,
        user_query: str,
        session_id: str = "default",
        *,
        on_trace: Callable[[str], None] | None = None,
    ) -> AgentResult:
        """Run a complete bounded ReAct loop and persist the conversation."""
        query = str(user_query).strip()
        if not query:
            return AgentResult(answer="Vui lòng nhập câu hỏi không rỗng.")

        # Load before storing the current message so memory does not duplicate it.
        memory_context = self.memory.context_for_prompt(
            session_id, MEMORY_MESSAGE_LIMIT
        )
        profile = self.memory.get_profile(session_id)
        self.memory.add_message(session_id, "user", query)

        scratchpad: list[str] = []
        trace: list[str] = _TraceRecorder(on_trace)
        tool_calls: list[ParsedAction] = []
        observations: list[tuple[ParsedAction, str]] = []
        action_counts: dict[tuple[str, str], int] = {}

        for iteration in range(1, self.max_iterations + 1):
            turn_prompt = self._build_turn_prompt(query, memory_context, scratchpad)
            try:
                output = _clean_llm_output(
                    self.provider.generate(turn_prompt, system_prompt=REACT_SYSTEM_PROMPT)
                )
            except ProviderError as exc:
                answer = f"Không thể kết nối LLM: {exc}"
                trace.append(f"Guardrail: {answer}")
                self.memory.add_message(
                    session_id,
                    "assistant",
                    answer,
                    {"guardrail": True, "iterations": iteration},
                )
                return AgentResult(
                    answer=answer,
                    trace=trace,
                    tool_calls=tool_calls,
                    iterations=iteration,
                    guardrail_triggered=True,
                )

            trace.extend(self._trace_from_output(output))
            final_answer = parse_final_answer(output)
            action: ParsedAction | None = None
            parse_error: str | None = None
            recovered_action = False
            try:
                action = parse_action(output)
            except ValueError as exc:
                parse_error = str(exc)

            if final_answer and action:
                parse_error = "Một lượt không được vừa có Action vừa có Final Answer."
                final_answer = None

            recovery_action = self._next_safe_action(query, profile, observations)
            terminal_error_answer = self._terminal_error_answer(observations)
            if final_answer:
                if terminal_error_answer:
                    final_answer = terminal_error_answer
                if recovery_action is not None:
                    trace.append(
                        "Guardrail: Final Answer bị chặn vì chưa đủ Observation; "
                        "ứng dụng khôi phục tool path."
                    )
                    action = recovery_action
                    trace.extend(
                        [
                            "Thought: Khôi phục protocol để lấy bằng chứng còn thiếu.",
                            f"Action: {action.raw}",
                        ]
                    )
                    final_answer = None
                    parse_error = None
                    recovered_action = True
                else:
                    grounded_answer = self._grounded_stock_answer(observations)
                    if grounded_answer:
                        return self._return_grounded_answer(
                            session_id,
                            grounded_answer,
                            trace,
                            tool_calls,
                            iteration,
                        )
                    guardrail_answer = self._grounding_guard_answer(
                        query, final_answer, profile, observations
                    )
                    if guardrail_answer:
                        return self._return_guardrail_answer(
                            session_id,
                            guardrail_answer,
                            trace,
                            tool_calls,
                            iteration,
                            "Chặn Final Answer chưa được grounding hoặc đang suy diễn ngoài DB.",
                        )
                    final_answer = self._enforce_advisory_scope(final_answer)
                    self.memory.add_message(
                        session_id,
                        "assistant",
                        final_answer,
                        {
                            "iterations": iteration,
                            "tool_calls": [item.raw for item in tool_calls],
                        },
                    )
                    return AgentResult(
                        answer=final_answer,
                        trace=trace,
                        tool_calls=tool_calls,
                        iterations=iteration,
                    )

            if (
                action is not None
                and recovery_action is not None
                and not self._action_matches_expected(action, recovery_action)
            ):
                trace.append(
                    f"Guardrail: Action '{action.raw}' không khớp tool path "
                    "được phép; ứng dụng thay bằng Action an toàn."
                )
                action = recovery_action
                trace.extend(
                    [
                        "Thought: Khôi phục protocol để lấy bằng chứng còn thiếu.",
                        f"Action: {action.raw}",
                    ]
                )
                parse_error = None
                recovered_action = True

            if action is None and recovery_action is not None:
                if parse_error:
                    trace.append(f"Observation: LỖI GIAO THỨC: {parse_error}")
                elif not parse_final_answer(output):
                    trace.append(
                        "Observation: LỖI GIAO THỨC: Thiếu Action hoặc "
                        "Final Answer; ứng dụng khôi phục tool path."
                    )
                action = recovery_action
                trace.extend(
                    [
                        "Thought: Khôi phục protocol để lấy bằng chứng còn thiếu.",
                        f"Action: {action.raw}",
                    ]
                )
                parse_error = None
                recovered_action = True

            if action is not None and recovery_action is None and terminal_error_answer:
                trace.extend(
                    [
                        f"Guardrail: Action '{action.raw}' bị chặn sau lỗi nghiệp vụ.",
                        f"Final Answer: {terminal_error_answer}",
                    ]
                )
                self.memory.add_message(
                    session_id,
                    "assistant",
                    terminal_error_answer,
                    {
                        "iterations": iteration,
                        "tool_calls": [item.raw for item in tool_calls],
                        "safe_fallback": True,
                    },
                )
                return AgentResult(
                    answer=terminal_error_answer,
                    trace=trace,
                    tool_calls=tool_calls,
                    iterations=iteration,
                )

            if action is None and recovery_action is None and terminal_error_answer:
                trace.append(f"Final Answer: {terminal_error_answer}")
                self.memory.add_message(
                    session_id,
                    "assistant",
                    terminal_error_answer,
                    {
                        "iterations": iteration,
                        "tool_calls": [item.raw for item in tool_calls],
                        "safe_fallback": True,
                    },
                )
                return AgentResult(
                    answer=terminal_error_answer,
                    trace=trace,
                    tool_calls=tool_calls,
                    iterations=iteration,
                )

            if action is None and not parse_error and output:
                # If no tool is missing, a free-form response can safely become
                # the final answer while the trace still records the normalization.
                grounded_answer = self._grounded_stock_answer(observations)
                if grounded_answer:
                    return self._return_grounded_answer(
                        session_id,
                        grounded_answer,
                        trace,
                        tool_calls,
                        iteration,
                    )
                guardrail_answer = self._grounding_guard_answer(
                    query, output, profile, observations
                )
                if guardrail_answer:
                    return self._return_guardrail_answer(
                        session_id,
                        guardrail_answer,
                        trace,
                        tool_calls,
                        iteration,
                        "Chặn free-form answer chưa được grounding hoặc đang suy diễn ngoài DB.",
                    )
                final_answer = self._enforce_advisory_scope(output)
                trace.append(f"Final Answer: {final_answer}")
                self.memory.add_message(
                    session_id,
                    "assistant",
                    final_answer,
                    {
                        "iterations": iteration,
                        "tool_calls": [item.raw for item in tool_calls],
                    },
                )
                return AgentResult(
                    answer=final_answer,
                    trace=trace,
                    tool_calls=tool_calls,
                    iterations=iteration,
                )

            if parse_error:
                observation = f"LỖI GIAO THỨC: {parse_error}"
                trace.append(f"Observation: {observation}")
                scratchpad.extend([output, f"Observation: {observation}"])
                continue

            if action is None:
                observation = (
                    "LỖI GIAO THỨC: Thiếu Action hoặc Final Answer. "
                    "Hãy dùng đúng một trong hai định dạng."
                )
                trace.append(f"Observation: {observation}")
                scratchpad.extend([output, f"Observation: {observation}"])
                continue

            action_key = self._normalized_action_key(action)
            action_counts[action_key] = action_counts.get(action_key, 0) + 1
            if action_counts[action_key] > MAX_REPEATED_ACTIONS:
                observation = (
                    f"LỖI GUARDRAIL: Action '{action.raw}' đã bị lặp. "
                    "Không gọi lại; hãy đổi hướng hoặc trả lời an toàn."
                )
            else:
                observation = self._execute_tool(action)
                tool_calls.append(action)
                self._save_grounded_profile(
                    session_id, query, action, observation
                )

            observation_line = f"Observation: {observation}"
            trace.append(observation_line)
            scratch_output = (
                "Thought: Khôi phục protocol để lấy bằng chứng còn thiếu.\n"
                f"Action: {action.raw}"
                if recovered_action
                else output
            )
            scratchpad.extend([scratch_output, observation_line])
            observations.append((action, observation))

        answer = self._safe_fallback(scratchpad)
        trace.append(
            f"Guardrail: Đạt MAX_ITERATIONS={self.max_iterations}; ngắt lặp an toàn."
        )
        self.memory.add_message(
            session_id,
            "assistant",
            answer,
            {
                "guardrail": True,
                "iterations": self.max_iterations,
                "tool_calls": [item.raw for item in tool_calls],
            },
        )
        return AgentResult(
            answer=answer,
            trace=trace,
            tool_calls=tool_calls,
            iterations=self.max_iterations,
            guardrail_triggered=True,
        )


def _print_agent_result(result: AgentResult) -> None:
    print("\n=== TRACE: Thought -> Action -> Observation ===")
    for line in result.trace:
        print(line)
    print("\n=== FINAL ===")
    print(result.answer)
    print(
        f"\nIterations: {result.iterations} | "
        f"Tool calls: {len(result.tool_calls)} | "
        f"Guardrail: {'TRIGGERED' if result.guardrail_triggered else 'OK'}"
    )


def _write_trace(path: Path, query: str, result: AgentResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "query": query,
        "trace": result.trace,
        "answer": result.answer,
        "iterations": result.iterations,
        "tool_calls": [item.raw for item in result.tool_calls],
        "guardrail_triggered": result.guardrail_triggered,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent-memory ReAct gift agent")
    parser.add_argument("--query", help="Câu hỏi tùy ý")
    parser.add_argument("--test-case", type=int, help="ID test case trong config")
    parser.add_argument("--mode", choices=("agent", "baseline"), default="agent")
    parser.add_argument("--session-id", default="demo-user")
    parser.add_argument("--show-memory", action="store_true")
    parser.add_argument("--clear-memory", action="store_true")
    parser.add_argument("--save-trace", type=Path)
    return parser


def main() -> int:
    args = build_argument_parser().parse_args()
    memory = SQLiteMemory()
    if args.clear_memory:
        memory.clear_session(args.session_id)
        print(f"Đã xóa memory của session '{args.session_id}'.")
        if not args.query and not args.test_case and not args.show_memory:
            return 0
    if args.show_memory:
        print(json.dumps(memory.get_profile(args.session_id), ensure_ascii=False, indent=2))
        if not args.query and not args.test_case:
            return 0

    tests = load_test_cases()
    query = args.query
    if args.test_case is not None:
        selected = next((case for case in tests if case["id"] == args.test_case), None)
        if selected is None:
            print(f"Không có test case ID={args.test_case}.", file=sys.stderr)
            return 2
        query = selected["question"]
    if not query:
        query = tests[3]["question"]

    try:
        provider = get_llm_provider()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"Provider={provider.__class__.__name__} | "
        f"Model={getattr(provider, 'model_name', 'unknown')} | "
        f"Session={args.session_id}"
    )
    if args.mode == "baseline":
        print(run_baseline_chatbot(query, provider))
        return 0

    result = GiftAdvisorAgent(provider, memory).run(query, args.session_id)
    _print_agent_result(result)
    if args.save_trace:
        _write_trace(args.save_trace, query, result)
        print(f"Đã lưu trace: {args.save_trace.resolve()}")
    return 1 if result.guardrail_triggered else 0


if __name__ == "__main__":
    raise SystemExit(main())
