"""Small browser UI for the persistent-memory gift advisor."""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
STATIC_DIR = SRC_DIR / "web_ui"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import GiftAdvisorAgent
from memory import SQLiteMemory
from providers import get_llm_provider


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return cleaned.strip("-")[:80] or "log"


def _create_log_file(log_dir: Path) -> Path:
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"web_{timestamp}.jsonl"


def _append_log(log_file: Path, event: str, **data: Any) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **data,
    }
    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _format_file_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _trace_kind(line: str) -> str:
    prefix = line.split(":", 1)[0].strip().lower()
    if prefix in {"thought", "action", "observation"}:
        return prefix
    if prefix == "guardrail":
        return "guardrail"
    if prefix == "final answer":
        return "final"
    return "trace"


def _answer_chunks(text: str, chunk_size: int = 18) -> list[str]:
    words = re.findall(r"\S+\s*", text)
    if not words:
        return [text] if text else []
    chunks = []
    current = ""
    for word in words:
        current += word
        if len(current) >= chunk_size:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    return chunks


class GiftAdvisorWebHandler(BaseHTTPRequestHandler):
    """HTTP endpoints plus static assets for the web UI."""

    memory: SQLiteMemory
    log_file: Path
    log_dir: Path
    provider_name: str | None
    default_session_id: str

    server_version = "GiftAdvisorWeb/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _send_sse(self, event: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _read_json_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json(400, "Content-Length không hợp lệ.")
            return None
        if length <= 0:
            return {}
        if length > 1_000_000:
            self._send_error_json(413, "Request quá lớn.")
            return None
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_error_json(400, "Body phải là JSON hợp lệ.")
            return None
        if not isinstance(payload, dict):
            self._send_error_json(400, "Body JSON phải là object.")
            return None
        return payload

    def _serve_static(self, request_path: str) -> None:
        relative_path = "index.html" if request_path == "/" else request_path.lstrip("/")
        static_root = STATIC_DIR.resolve()
        target = (STATIC_DIR / relative_path).resolve()
        if static_root not in target.parents and target != static_root:
            self.send_error(404)
            return
        if not target.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in {"/", "/app.css", "/app.js"}:
            self._serve_static(path)
            return
        if path == "/api/state":
            self._handle_state()
            return
        if path == "/api/memory":
            self._handle_memory(parsed.query)
            return
        if path == "/api/logs":
            self._handle_logs()
            return
        if path == "/api/log-file":
            self._handle_log_file(parsed.query)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            self._handle_chat()
            return
        if parsed.path == "/api/chat/stream":
            self._handle_chat_stream()
            return
        if parsed.path == "/api/memory/clear":
            self._handle_clear_memory()
            return
        self.send_error(404)

    def _handle_state(self) -> None:
        try:
            provider = get_llm_provider(self.provider_name)
            provider_info = {
                "name": provider.__class__.__name__,
                "model": getattr(provider, "model_name", "unknown"),
            }
        except ValueError as exc:
            provider_info = {"error": str(exc)}
        self._send_json(
            200,
            {
                "default_session_id": self.default_session_id,
                "provider": provider_info,
                "log_file": str(self.log_file),
            },
        )

    def _handle_memory(self, query_string: str) -> None:
        params = parse_qs(query_string)
        session_id = (params.get("session_id") or [self.default_session_id])[0].strip()
        try:
            profile = self.memory.get_profile(session_id)
            messages = self.memory.recent_messages(session_id, limit=12)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        self._send_json(200, {"profile": profile, "messages": messages})

    def _handle_logs(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for path in sorted(
            self.log_dir.glob("*.jsonl"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            files.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "modified_at": _format_file_time(path),
                    "current": path.resolve() == self.log_file.resolve(),
                }
            )
        self._send_json(200, {"files": files})

    def _handle_log_file(self, query_string: str) -> None:
        params = parse_qs(query_string)
        requested_name = (params.get("name") or [""])[0].strip()
        if not requested_name:
            self._send_error_json(400, "Thiếu tên file log.")
            return
        safe_name = _safe_filename(requested_name)
        if safe_name != requested_name or not safe_name.endswith(".jsonl"):
            self._send_error_json(400, "Tên file log không hợp lệ.")
            return
        target = (self.log_dir / safe_name).resolve()
        if self.log_dir.resolve() not in target.parents or not target.is_file():
            self._send_error_json(404, "Không tìm thấy file log.")
            return

        lines = target.read_text(encoding="utf-8").splitlines()
        visible_lines = lines[-300:]
        entries = []
        for index, line in enumerate(visible_lines, start=max(1, len(lines) - 299)):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                entry = {"event": "invalid_json", "raw": line}
            entry["_line"] = index
            entries.append(entry)
        self._send_json(
            200,
            {
                "name": target.name,
                "entries": entries,
                "truncated": len(lines) > len(visible_lines),
            },
        )

    def _handle_chat(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        message = str(payload.get("message", "")).strip()
        session_id = str(payload.get("session_id", self.default_session_id)).strip()
        provider_override = str(payload.get("provider", "")).strip() or self.provider_name
        if provider_override == "default":
            provider_override = self.provider_name
        if not message:
            self._send_error_json(400, "Tin nhắn không được để trống.")
            return
        try:
            provider = get_llm_provider(provider_override)
            agent = GiftAdvisorAgent(provider, self.memory)
            result = agent.run(message, session_id=session_id)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        except Exception as exc:
            self._send_error_json(500, f"Không chạy được agent: {type(exc).__name__}: {exc}")
            return

        response = {
            "answer": result.answer,
            "trace": result.trace,
            "iterations": result.iterations,
            "tool_calls": [item.raw for item in result.tool_calls],
            "guardrail_triggered": result.guardrail_triggered,
            "provider": provider.__class__.__name__,
            "model": getattr(provider, "model_name", "unknown"),
        }
        _append_log(
            self.log_file,
            "agent_turn",
            session_id=session_id,
            query=message,
            **response,
        )
        self._send_json(200, response)

    def _handle_chat_stream(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        message = str(payload.get("message", "")).strip()
        session_id = str(payload.get("session_id", self.default_session_id)).strip()
        provider_override = str(payload.get("provider", "")).strip() or self.provider_name
        if provider_override == "default":
            provider_override = self.provider_name
        if not message:
            self._send_error_json(400, "Tin nhắn không được để trống.")
            return

        self._send_sse_headers()
        try:
            provider = get_llm_provider(provider_override)
            self._send_sse(
                "status",
                {
                    "message": "Agent đang suy luận...",
                    "provider": provider.__class__.__name__,
                    "model": getattr(provider, "model_name", "unknown"),
                    "session_id": session_id,
                },
            )

            def on_trace(line: str) -> None:
                self._send_sse(
                    "thought",
                    {
                        "line": line,
                        "kind": _trace_kind(line),
                    },
                )

            agent = GiftAdvisorAgent(provider, self.memory)
            result = agent.run(message, session_id=session_id, on_trace=on_trace)
        except ValueError as exc:
            self._send_sse("error", {"message": str(exc)})
            self._send_sse("done", {"ok": False})
            return
        except Exception as exc:
            self._send_sse(
                "error",
                {"message": f"Không chạy được agent: {type(exc).__name__}: {exc}"},
            )
            self._send_sse("done", {"ok": False})
            return

        response = {
            "answer": result.answer,
            "trace": result.trace,
            "iterations": result.iterations,
            "tool_calls": [item.raw for item in result.tool_calls],
            "guardrail_triggered": result.guardrail_triggered,
            "provider": provider.__class__.__name__,
            "model": getattr(provider, "model_name", "unknown"),
        }
        _append_log(
            self.log_file,
            "agent_stream_turn",
            session_id=session_id,
            query=message,
            **response,
        )
        for chunk in _answer_chunks(result.answer):
            self._send_sse("answer_delta", {"text": chunk})
        self._send_sse("done", {"ok": True, **response})

    def _handle_clear_memory(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return
        session_id = str(payload.get("session_id", self.default_session_id)).strip()
        try:
            self.memory.clear_session(session_id)
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        _append_log(self.log_file, "memory_cleared", session_id=session_id)
        self._send_json(200, {"ok": True})


def _build_handler(
    *,
    memory: SQLiteMemory,
    log_file: Path,
    log_dir: Path,
    provider_name: str | None,
    default_session_id: str,
) -> type[GiftAdvisorWebHandler]:
    class ConfiguredGiftAdvisorWebHandler(GiftAdvisorWebHandler):
        pass

    ConfiguredGiftAdvisorWebHandler.memory = memory
    ConfiguredGiftAdvisorWebHandler.log_file = log_file
    ConfiguredGiftAdvisorWebHandler.log_dir = log_dir.resolve()
    ConfiguredGiftAdvisorWebHandler.provider_name = provider_name
    ConfiguredGiftAdvisorWebHandler.default_session_id = default_session_id
    return ConfiguredGiftAdvisorWebHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gift Advisor browser UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--session-id", default="default-user")
    parser.add_argument("--provider", help="Ghi đè LLM_PROVIDER, ví dụ: vilao hoặc mock")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    return parser


def _create_server(
    host: str,
    port: int,
    handler: type[GiftAdvisorWebHandler],
) -> tuple[ThreadingHTTPServer, str | None]:
    try:
        return ThreadingHTTPServer((host, port), handler), None
    except OSError as exc:
        if port == 0:
            raise
        server = ThreadingHTTPServer((host, 0), handler)
        fallback_port = server.server_address[1]
        message = f"Port {port} đang bận ({exc}); chuyển sang port {fallback_port}."
        return server, message


def main() -> int:
    args = build_parser().parse_args()
    log_dir = args.log_dir if args.log_dir.is_absolute() else PROJECT_ROOT / args.log_dir
    log_file = _create_log_file(log_dir)
    memory = SQLiteMemory()
    handler = _build_handler(
        memory=memory,
        log_file=log_file,
        log_dir=log_dir,
        provider_name=args.provider,
        default_session_id=args.session_id.strip() or "default-user",
    )
    server, fallback_message = _create_server(args.host, args.port, handler)
    actual_host, actual_port = server.server_address[:2]
    url_host = args.host if actual_host in {"0.0.0.0", "::"} else actual_host
    url = f"http://{url_host}:{actual_port}"
    _append_log(
        log_file,
        "web_started",
        session_id=args.session_id,
        provider=args.provider or "env/default",
        url=url,
        port=actual_port,
    )
    print("=" * 60)
    print("GIFT ADVISOR WEB UI")
    if fallback_message:
        print(fallback_message)
    print(f"Open: {url}")
    print(f"Log: {log_file}")
    print("Press Ctrl+C to stop.")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web UI...")
    finally:
        _append_log(log_file, "web_stopped", session_id=args.session_id)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
