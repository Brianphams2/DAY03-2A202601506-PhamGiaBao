"""Interactive command-line interface for the persistent-memory gift agent."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import GiftAdvisorAgent, load_test_cases
from memory import SQLiteMemory
from providers import get_llm_provider


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


HELP_TEXT = """
Lệnh CLI:
  /help                 Hiển thị trợ giúp
  /memory               Xem hồ sơ SQLite memory của session hiện tại
  /clear                Xóa memory của session hiện tại
  /session <id>         Chuyển sang session khác
  /test <id>            Chạy một test case trong config/test_cases.json
  /trace on|off         Bật/tắt hiển thị Thought/Action/Observation
  /exit                  Thoát chương trình

Nhập văn bản bình thường để trò chuyện với agent.
""".strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gift Advisor interactive CLI")
    parser.add_argument("--session-id", default="default-user")
    parser.add_argument(
        "--provider",
        help="Ghi đè LLM_PROVIDER, ví dụ: vilao hoặc mock",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Hiển thị trace ReAct cho mỗi câu trả lời",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help="Thư mục lưu CLI logs (mặc định: logs)",
    )
    return parser


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    return cleaned.strip("-")[:50] or "default-user"


def _create_log_file(log_dir: Path, session_id: str) -> Path:
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"cli_{_safe_filename(session_id)}_{timestamp}.jsonl"


def _append_log(log_file: Path, event: str, **data: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }
    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _print_trace(result) -> None:
    print("\n[TRACE]")
    for line in result.trace:
        print(f"  {line}")
    print(
        f"  Iterations={result.iterations} | "
        f"Tool calls={len(result.tool_calls)} | "
        f"Guardrail={'ON' if result.guardrail_triggered else 'OK'}"
    )


def _resolve_test_query(raw_id: str) -> str | None:
    try:
        test_id = int(raw_id)
    except ValueError:
        print("ID test case phải là số.")
        return None
    selected = next(
        (case for case in load_test_cases() if case["id"] == test_id),
        None,
    )
    if selected is None:
        print(f"Không tìm thấy test case ID={test_id}.")
        return None
    print(f"[TEST CASE {test_id}] {selected['question']}")
    return selected["question"]


def main() -> int:
    args = build_parser().parse_args()
    try:
        provider = get_llm_provider(args.provider)
    except ValueError as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return 2

    memory = SQLiteMemory()
    agent = GiftAdvisorAgent(provider, memory)
    session_id = args.session_id.strip() or "default-user"
    show_trace = args.trace
    log_file = _create_log_file(args.log_dir, session_id)
    provider_name = provider.__class__.__name__
    model_name = getattr(provider, "model_name", "unknown")
    _append_log(
        log_file,
        "cli_started",
        session_id=session_id,
        provider=provider_name,
        model=model_name,
    )

    print("=" * 60)
    print("GIFT ADVISOR CLI — ReAct Agent + Local SQLite Memory")
    print(
        f"Provider: {provider_name} | "
        f"Model: {model_name}"
    )
    print(f"Session: {session_id}")
    print(f"Log: {log_file}")
    print("Gõ /help để xem lệnh, /exit để thoát.")
    print("=" * 60)

    while True:
        try:
            user_input = input(f"\n[{session_id}] Bạn > ").strip()
        except (EOFError, KeyboardInterrupt):
            _append_log(log_file, "cli_stopped", session_id=session_id)
            print("\nĐã thoát CLI.")
            return 0

        if not user_input:
            continue
        if user_input.lower() in {"/exit", "/quit", "exit", "quit"}:
            _append_log(log_file, "cli_stopped", session_id=session_id)
            print("Đã thoát CLI.")
            return 0
        if user_input.lower() == "/help":
            print(HELP_TEXT)
            continue
        if user_input.lower() == "/memory":
            profile = memory.get_profile(session_id)
            print(
                json.dumps(profile, ensure_ascii=False, indent=2)
                if profile
                else "Session này chưa có hồ sơ memory."
            )
            continue
        if user_input.lower() == "/clear":
            memory.clear_session(session_id)
            _append_log(log_file, "memory_cleared", session_id=session_id)
            print(f"Đã xóa memory của session '{session_id}'.")
            continue
        if user_input.lower().startswith("/session"):
            parts = user_input.split(maxsplit=1)
            if len(parts) == 1 or not parts[1].strip():
                print("Cú pháp: /session <id>")
                continue
            previous_session_id = session_id
            session_id = parts[1].strip()
            _append_log(
                log_file,
                "session_changed",
                previous_session_id=previous_session_id,
                session_id=session_id,
            )
            print(f"Đã chuyển sang session '{session_id}'.")
            continue
        if user_input.lower().startswith("/trace"):
            parts = user_input.lower().split(maxsplit=1)
            if len(parts) != 2 or parts[1] not in {"on", "off"}:
                print("Cú pháp: /trace on hoặc /trace off")
                continue
            show_trace = parts[1] == "on"
            print(f"Trace: {'ON' if show_trace else 'OFF'}")
            continue
        if user_input.lower().startswith("/test"):
            parts = user_input.split(maxsplit=1)
            if len(parts) != 2:
                print("Cú pháp: /test <id>")
                continue
            query = _resolve_test_query(parts[1])
            if query is None:
                continue
        elif user_input.startswith("/"):
            print("Lệnh không hợp lệ. Gõ /help để xem danh sách.")
            continue
        else:
            query = user_input

        result = agent.run(query, session_id=session_id)
        _append_log(
            log_file,
            "agent_turn",
            session_id=session_id,
            query=query,
            trace=result.trace,
            answer=result.answer,
            iterations=result.iterations,
            tool_calls=[item.raw for item in result.tool_calls],
            guardrail_triggered=result.guardrail_triggered,
        )
        if show_trace:
            _print_trace(result)
        print(f"\nAgent > {result.answer}")


if __name__ == "__main__":
    raise SystemExit(main())
