"""IPython cell magic helpers for cleon."""

from __future__ import annotations

import base64
import html
import importlib.resources as importlib_resources
import json
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

try:  # pragma: no cover - optional import when IPython is available
    from IPython import get_ipython  # type: ignore
    from IPython.display import Markdown, display, HTML, update_display  # type: ignore
except Exception:  # pragma: no cover - fallback when IPython is missing

    def get_ipython():  # type: ignore
        return None

    def display(*_: object, **__: object) -> None:  # type: ignore
        pass

    def update_display(*_: object, **__: object) -> None:  # type: ignore
        pass

    class HTML:  # type: ignore
        def __init__(self, data: str) -> None:
            self.data = data

    class Markdown:  # type: ignore
        def __init__(self, data: str) -> None:
            self.data = data


from collections import deque

from .backend import AgentBackend, resolve_backend
from .settings import (
    get_agent_prefix,
    get_agent_theme,
    get_session_store_path,
    template_for_agent,
    status_summary,
    get_default_mode,
    add_mode as settings_add_mode,
    default_mode as settings_default_mode,
    reset_settings as settings_reset,
    plain_text_output,
)

DisplayMode = str
_BACKENDS: dict[str, AgentBackend] = {}
_ACTIVE_BACKEND: AgentBackend | None = None
_ACTIVE_BACKEND_NAME: str | None = None
_AGENT_HISTORY: Any = {}
_HISTORY_LIMIT = 8
_BASE_STYLE_EMITTED = False
_LOG_PATH: str | None = None
_CONVERSATION_LOG_PATH: str | None = None
_CANCEL_PATH: str | None = None
_CONTEXT_TRACKER: "ContextTracker | None" = None
_ASYNC_MODE: bool = False
_CODEX_QUEUE: "queue.Queue[CodexRequest | None] | None" = None
_WORKER_THREAD: threading.Thread | None = None
_AUTO_ROUTE_INSTALLED = False
_ORIG_RUN_CELL = None
_AUTO_ROUTE_RULES: dict[str, tuple[str, str]] = {}
_CANCELLED_REQUESTS: set[str] = set()
_CANCELLED_LOCK = threading.Lock()
_CANCEL_ALL = threading.Event()
_PENDING_REQUESTS: dict[str, str] = {}
_ACTIVE_ASYNC_COUNT = 0
_ACTIVE_ASYNC_LOCK = threading.Lock()
_ASYNC_IDLE = threading.Event()
_ASYNC_IDLE.set()
_AGENT_HISTORY_LOCK = threading.Lock()
_CONTEXT_LOCK = threading.Lock()

_AGENT_THEMES = {
    "codex": {
        "light_bg": "#1F1F1F",
        "light_border": "#3A3A3A",
        "light_color": "#F5F5F5",
        "dark_bg": "#1F1F1F",
        "dark_border": "#3A3A3A",
        "dark_color": "#F5F5F5",
    },
    "claude": {
        "light_bg": "#262624",
        "light_border": "#4A4A45",
        "light_color": "#F7F5F2",
        "dark_bg": "#262624",
        "dark_border": "#4A4A45",
        "dark_color": "#F7F5F2",
    },
    "default": {
        "light_bg": "#1F1F23",
        "light_border": "#3a3a40",
        "light_color": "#F5F5F5",
        "dark_bg": "#1F1F23",
        "dark_border": "#3a3a40",
        "dark_color": "#F5F5F5",
    },
}

_AGENT_ICON_PATHS = {
    "codex": "images/codex-white.png",
    "claude": "images/claude.png",
}
_ICON_STYLES_APPLIED = False


@dataclass
class CodexRequest:
    """Request to process a %%codex cell in background."""

    prompt: str
    display_id: str
    request_id: str
    status_text: str
    context_cells: int | None
    context_chars: int | None
    mode: str
    emit_events: bool
    backend: AgentBackend


def use(
    name: str = "codex",
    *,
    agent: str | None = None,
    binary: str | None = None,
    env: Mapping[str, str] | None = None,
    display_mode: DisplayMode = "auto",
    show_events: bool = False,
    debug: bool = False,
    stream: bool = True,
    prompt_user: bool = False,
    log_path: str | os.PathLike[str] | None = "./cleon.log",
    cancel_path: str | os.PathLike[str] | None = None,
    context_changes: bool = True,
    context_cells: int | None = None,
    context_chars: int | None = None,
    async_mode: bool = True,
    auto: bool = True,
    session_id: str | None = None,
    ipython=None,
) -> Callable[[str, str | None], Any]:
    """High-level helper to expose ``%%name`` in the current IPython shell."""

    if agent and name == "codex":
        name = agent

    register_magic(
        name=name,
        agent=agent,
        binary=binary,
        env=env,
        display_mode=display_mode,
        show_events=show_events,
        debug=debug,
        stream=stream,
        prompt_user=prompt_user,
        log_path=log_path,
        cancel_path=cancel_path,
        context_changes=context_changes,
        context_cells=context_cells,
        context_chars=context_chars,
        async_mode=async_mode,
        auto=auto,
        session_id=session_id,
        ipython=ipython,
    )
    return None  # type: ignore[return-value]


def _register_backend(name: str, backend: AgentBackend) -> None:
    global _ACTIVE_BACKEND, _ACTIVE_BACKEND_NAME
    _BACKENDS[name] = backend
    if _ACTIVE_BACKEND is None:
        _ACTIVE_BACKEND = backend
        _ACTIVE_BACKEND_NAME = name
    _ensure_base_style()


def _select_backend(name: str) -> AgentBackend:
    backend = _BACKENDS.get(name)
    if backend is None:
        raise RuntimeError(
            f"cleon backend '{name}' is not available. Run cleon.use(agent='{name}') first."
        )
    global _ACTIVE_BACKEND, _ACTIVE_BACKEND_NAME
    _ACTIVE_BACKEND = backend
    _ACTIVE_BACKEND_NAME = name
    return backend


def _require_backend(name: str | None = None) -> AgentBackend:
    if name:
        return _select_backend(name)
    if _ACTIVE_BACKEND is None:
        msg = (
            "<div style='background:#222;color:#f5f5f5;padding:12px;border-radius:6px;'>"
            "<strong>cleon session not active.</strong><br/>"
            "Run <code>cleon.use()</code> or <code>cleon.resume()</code> to start a session."
            "</div>"
        )
        try:
            display(HTML(msg))
        except Exception:
            print(
                "cleon session not active. Run cleon.use(...) or cleon.resume(...) first."
            )
        raise RuntimeError("cleon session not active.")
    return _ACTIVE_BACKEND


def _ensure_base_style() -> None:
    global _BASE_STYLE_EMITTED
    if _BASE_STYLE_EMITTED:
        return
    css = """
<style>
.cleon-icon {
    position:absolute;
    top:8px;
    right:10px;
    width:18px;
    height:18px;
    opacity:0.7;
    background-size:contain;
    background-repeat:no-repeat;
    pointer-events:none;
}
</style>
"""
    try:
        display(HTML(css))
        _BASE_STYLE_EMITTED = True
    except Exception:
        pass


def _default_agent_name(agent: str | None = None) -> str:
    if agent:
        return agent.lower()
    if _ACTIVE_BACKEND_NAME:
        return _ACTIVE_BACKEND_NAME
    if "codex" in _BACKENDS:
        return "codex"
    if _BACKENDS:
        return next(iter(_BACKENDS.keys()))
    return "codex"


def _mark_async_start() -> None:
    global _ACTIVE_ASYNC_COUNT
    with _ACTIVE_ASYNC_LOCK:
        _ACTIVE_ASYNC_COUNT += 1
        _ASYNC_IDLE.clear()


def _mark_async_done() -> None:
    global _ACTIVE_ASYNC_COUNT
    with _ACTIVE_ASYNC_LOCK:
        _ACTIVE_ASYNC_COUNT = max(0, _ACTIVE_ASYNC_COUNT - 1)
        if (_CODEX_QUEUE is None or _CODEX_QUEUE.empty()) and _ACTIVE_ASYNC_COUNT == 0:
            _ASYNC_IDLE.set()


def _wait_for_async_tasks() -> None:
    if not _ASYNC_MODE:
        return
    while True:
        queue_empty = _CODEX_QUEUE is None or _CODEX_QUEUE.empty()
        with _ACTIVE_ASYNC_LOCK:
            active = _ACTIVE_ASYNC_COUNT
        if queue_empty and active == 0:
            _ASYNC_IDLE.set()
            return
        time.sleep(0.05)


def _active_async_requests() -> int:
    with _ACTIVE_ASYNC_LOCK:
        return _ACTIVE_ASYNC_COUNT


def _worker_loop() -> None:
    """Background worker that processes codex requests from queue."""
    global _CODEX_QUEUE
    while True:
        try:
            if _CODEX_QUEUE is None or _CANCEL_ALL.is_set():
                break
            request = _CODEX_QUEUE.get(timeout=0.5)
            if request is None:  # Poison pill to stop worker
                break
            with _CANCELLED_LOCK:
                if request.request_id in _CANCELLED_REQUESTS:
                    _render_async_status(
                        request.display_id,
                        request.request_id,
                        "Cancelled.",
                        cancellable=False,
                    )
                    _CANCELLED_REQUESTS.discard(request.request_id)
                    _PENDING_REQUESTS.pop(request.request_id, None)
                    continue
                if _CANCEL_ALL.is_set():
                    _render_async_status(
                        request.display_id,
                        request.request_id,
                        "Cancelled.",
                        cancellable=False,
                    )
                    _PENDING_REQUESTS.pop(request.request_id, None)
                    continue
            _mark_async_start()
            try:
                _process_codex_request(request)
            finally:
                _mark_async_done()
                with _CANCELLED_LOCK:
                    _PENDING_REQUESTS.pop(request.request_id, None)
        except queue.Empty:
            continue
        except Exception:
            # Log error but keep worker running
            pass


def _start_worker_thread() -> None:
    """Start background worker thread if not already running."""
    global _CODEX_QUEUE, _WORKER_THREAD
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return
    _CANCEL_ALL.clear()
    if _CODEX_QUEUE is None:
        _CODEX_QUEUE = queue.Queue()
    _WORKER_THREAD = threading.Thread(
        target=_worker_loop, daemon=True, name="codex-worker"
    )
    _WORKER_THREAD.start()


def _stop_worker_thread() -> None:
    """Stop background worker thread cleanly."""
    global _CODEX_QUEUE, _WORKER_THREAD
    if _CODEX_QUEUE is not None:
        _CODEX_QUEUE.put(None)  # Poison pill
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        _WORKER_THREAD.join(timeout=2.0)
    _CODEX_QUEUE = None
    _WORKER_THREAD = None
    _ASYNC_IDLE.set()


def _cancel_request(request_id: str, display_id: str) -> None:
    with _CANCELLED_LOCK:
        _CANCELLED_REQUESTS.add(request_id)
        _PENDING_REQUESTS.pop(request_id, None)
    try:
        update_display(
            HTML('<div style="color: #888;">Cancelled.</div>'), display_id=display_id
        )
    except Exception:
        pass


def _cancel_all(display_id: str | None = None) -> None:
    _CANCEL_ALL.set()
    with _CANCELLED_LOCK:
        for rid in list(_PENDING_REQUESTS.keys()):
            _CANCELLED_REQUESTS.add(rid)
            disp_id = _PENDING_REQUESTS.get(rid)
            if disp_id:
                try:
                    update_display(
                        HTML('<div style="color: #888;">Cancelled all.</div>'),
                        display_id=disp_id,
                    )
                except Exception:
                    pass
        _PENDING_REQUESTS.clear()
    # Drain any queued requests
    if _CODEX_QUEUE is not None:
        try:
            while True:
                req = _CODEX_QUEUE.get_nowait()
                if isinstance(req, CodexRequest):
                    try:
                        update_display(
                            HTML('<div style="color: #888;">Cancelled all.</div>'),
                            display_id=req.display_id,
                        )
                    except Exception:
                        pass
                if req is None:
                    break
        except queue.Empty:
            pass
    if display_id:
        try:
            update_display(
                HTML('<div style="color: #888;">Cancelled all.</div>'),
                display_id=display_id,
            )
        except Exception:
            pass
    if _ACTIVE_ASYNC_COUNT == 0:
        _ASYNC_IDLE.set()


def _reset_cancellations() -> None:
    _CANCEL_ALL.clear()
    with _CANCELLED_LOCK:
        _CANCELLED_REQUESTS.clear()
        _PENDING_REQUESTS.clear()
    _start_worker_thread()
    if _CODEX_QUEUE is None or _CODEX_QUEUE.empty():
        _ASYNC_IDLE.set()


def _session_store_path() -> Path:
    path = get_session_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_session_store() -> dict[str, dict[str, str]]:
    try:
        path = _session_store_path()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                # Ensure JSON types are strings for mypy; coerce non-strings
                sanitized: dict[str, dict[str, str]] = {}
                for key, value in raw.items():
                    if isinstance(value, dict):
                        inner: dict[str, str] = {}
                        for k, v in value.items():
                            inner[str(k)] = str(v)
                        sanitized[str(key)] = inner
                return sanitized
    except Exception:
        pass
    return {}


def _persist_session_id(session_id: str, resume_cmd: str | None, agent: str) -> None:
    try:
        store = _load_session_store()
        key = _get_notebook_name() or "default"
        store[key] = {
            "session_id": session_id,
            "resume_command": resume_cmd or "",
            "agent": agent,
            "notebook": key,
            "timestamp": f"{time.time()}",
        }
        store["_last"] = {
            "session_id": session_id,
            "resume_command": resume_cmd or "",
            "agent": agent,
            "notebook": key,
            "timestamp": f"{time.time()}",
        }
        path = _session_store_path()
        path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _render_async_status(
    display_id: str, request_id: str, status: str, *, cancellable: bool
) -> None:
    try:
        import ipywidgets as widgets  # type: ignore
    except Exception:
        display(
            HTML(f'<div style="color: #888;">{status}</div>'), display_id=display_id
        )
        return

    status_html = widgets.HTML(f'<div style="color: #888;">{status}</div>')
    if cancellable:
        btn = widgets.Button(
            description="Cancel",
            button_style="warning",
            layout=widgets.Layout(width="70px", height="28px", padding="0px 4px"),
        )
        btn.on_click(lambda _b: _cancel_request(request_id, display_id))
        cancel_all = widgets.Button(
            description="Cancel All",
            button_style="warning",
            layout=widgets.Layout(width="90px", height="28px", padding="0px 4px"),
        )
        cancel_all.on_click(lambda _b: _cancel_all(display_id))
        display(widgets.HBox([status_html, btn, cancel_all]), display_id=display_id)
    else:
        display(status_html, display_id=display_id)


def _process_codex_request(request: CodexRequest) -> None:
    """Process a single codex request and update display."""

    backend = request.backend
    agent_name = getattr(backend, "name", "codex")

    def _cancel() -> None:
        _stop_session(backend=backend, agent=agent_name, force=True)

    progress = _Progress(
        render=request.mode != "none",
        _cancel=_cancel,
        display_id=request.display_id,
        initial_message=request.status_text or "Processing...",
    )
    try:
        with _CANCELLED_LOCK:
            if _CANCEL_ALL.is_set():
                progress.finish("Cancelled.", markdown=False)
                _PENDING_REQUESTS.pop(request.request_id, None)
                return
            if request.request_id in _CANCELLED_REQUESTS:
                progress.finish("Cancelled.", markdown=False)
                _CANCELLED_REQUESTS.discard(request.request_id)
                _PENDING_REQUESTS.pop(request.request_id, None)
                return
        # Build full prompt with template/context
        parts = []

        # 1. Template (first turn only)
        if backend.first_turn():
            template = _resolve_template(agent_name)
            if template:
                _log_template(template)
                parts.append(template)

        # 2. Context (if enabled)
        if _CONTEXT_TRACKER is not None:
            context_block = _build_context_block(
                request.context_cells, request.context_chars, agent_name
            )
            if context_block:
                _log_context_block(context_block)
                parts.append(f"Context (changed cells):\n{context_block}")

        history_block = _build_interagent_context(agent_name)
        if history_block:
            parts.append(history_block)

        # 3. User prompt
        if parts:
            parts.append(f"User prompt:\n{request.prompt}")
            full_prompt = "\n\n".join(parts)
        else:
            full_prompt = request.prompt

        _log_prompt(full_prompt)

        # Send to backend
        result, events = backend.send(
            full_prompt,
            on_event=_chain(progress.update, _log_event),
            on_approval=_prompt_approval,
        )

        # Extract response and log
        response = _extract_final_message(result)
        _log_conversation(full_prompt, response)
        _record_agent_history(agent_name, request.prompt, response)

        # Update cell display with result
        if request.mode != "none":
            _display_result(result, request.mode, progress, agent_name)

        if request.emit_events:
            update_display(events, display_id=f"{request.display_id}-events")

    except Exception as e:
        # Show error in cell
        error_msg = f"❌ Codex error: {e}"
        if progress.handle is not None:
            progress.finish(error_msg, markdown=False)
        else:
            update_display(error_msg, display_id=request.display_id)


def register_magic(
    *,
    name: str = "codex",
    agent: str | None = None,
    binary: str | None = None,
    env: Mapping[str, str] | None = None,
    display_mode: DisplayMode = "auto",
    show_events: bool = False,
    debug: bool = False,
    stream: bool = True,
    prompt_user: bool = False,
    log_path: str | os.PathLike[str] | None = "./cleon.log",
    cancel_path: str | os.PathLike[str] | None = None,
    context_changes: bool = True,
    context_cells: int | None = None,
    context_chars: int | None = None,
    async_mode: bool = True,
    auto: bool = True,
    session_id: str | None = None,
    ipython=None,
) -> Callable[[str, str | None], Any]:
    """Register the ``%%name`` cell magic for cleon."""

    ip = _ensure_ipython(ipython)
    normalized = name.lower()
    mode = display_mode.lower()
    if mode not in {"auto", "markdown", "text", "none"}:
        raise ValueError(
            "display_mode must be one of 'auto', 'markdown', 'text', or 'none'"
        )

    backend_name = (agent or normalized).lower()
    backend = resolve_backend(
        agent=backend_name, binary=binary, extra_env=env, session_id=session_id
    )
    _register_backend(normalized, backend)
    agent_prefix = get_agent_prefix(backend_name)

    # Only log by default when debug/show_events is on; otherwise keep logs off unless explicitly set
    effective_log_path = (
        log_path
        if log_path is not None
        else ("./cleon.log" if debug or show_events else None)
    )
    _configure_logging(effective_log_path)
    _configure_conversation_log()
    _configure_cancel(cancel_path)
    if context_changes:
        _configure_context()

    # Configure async mode
    global _ASYNC_MODE
    if async_mode and not getattr(backend, "supports_async", True):
        print(
            f"Backend '{backend_name}' does not support async mode; falling back to synchronous turns."
        )
        async_mode = False
    _ASYNC_MODE = async_mode
    if async_mode:
        _start_worker_thread()
        # Register cleanup handler for kernel shutdown
        try:
            import atexit

            atexit.register(_stop_worker_thread)
        except Exception:
            pass

    emit_events = show_events or debug

    def _codex_magic(line: str, cell: str | None = None) -> Any:
        prompt = _normalize_payload(line, cell)
        if not prompt:
            print("No prompt provided.")
            return None

        raw_user_prompt = prompt
        active_backend = _require_backend(normalized)
        active_agent_name = getattr(active_backend, "name", "codex")

        # Command prefixes for mode control
        if prompt.startswith("/"):
            cmd, _, rest = prompt.partition(" ")
            cmd = cmd.lower()

            def _cancel() -> None:
                _stop_session(
                    agent=normalized,
                    backend=active_backend,
                    force=True,
                    wait_for_tasks=False,
                )

            progress = _Progress(render=stream, _cancel=_cancel)

            # One-shot prompt (fresh process)
            if cmd in {"/fresh", "/once"}:
                payload = rest.strip()
                if not payload:
                    print("Usage: /fresh <prompt>")
                    return None
                result, events = active_backend.run_once(payload)
                _log_events(events)
                if mode != "none":
                    _display_result(result, mode, progress, active_agent_name)
                if emit_events:
                    _print_events(events)
                return result if emit_events else None

            if cmd == "/stop":
                stop(agent=normalized)
                if async_mode:
                    print("cleon session and async worker stopped.")
                else:
                    print("cleon session stopped.")
                return None

            if cmd == "/status":
                alive = _session_alive()
                print(f"cleon session: {'running' if alive else 'stopped'}")
                return alive

            if cmd == "/new":
                _stop_session(
                    agent=normalized,
                    backend=active_backend,
                    keep_backend=True,
                    force=True,
                    wait_for_tasks=False,
                )
                active_backend = _require_backend(normalized)
                active_agent_name = getattr(active_backend, "name", "codex")
                result, events = active_backend.send(
                    rest.strip(),
                    on_event=_chain(progress.update, _log_event),
                    on_approval=_prompt_approval,
                )
                if mode != "none":
                    _display_result(result, mode, progress, active_agent_name)
                if emit_events:
                    _print_events(events)
                return result if emit_events else None

            if cmd == "/peek_history":
                if not context_changes:
                    print(
                        "Context tracking not enabled. Use cleon.use(..., context_changes=True)"
                    )
                    return None
                block = _build_context_block(
                    context_cells, context_chars, active_agent_name, peek=True
                )
                if block:
                    print("Preview of context for next %%codex turn:\n")
                    print(block)
                else:
                    print("No changed cells detected.")
                return block

            print(f"Unknown command: {cmd}")
            print("Commands: /fresh, /stop, /status, /new, /peek_history")
            return None

        # Async mode: queue request and return immediately
        if async_mode:
            _reset_cancellations()
            display_id = f"codex-{uuid.uuid4().hex[:8]}"
            request_id = f"req-{uuid.uuid4().hex[:8]}"
            with _CANCELLED_LOCK:
                _PENDING_REQUESTS[request_id] = display_id

            # Show queue position
            queue_size = _CODEX_QUEUE.qsize() if _CODEX_QUEUE else 0
            if queue_size > 0:
                status = f"⏳ Queued (position {queue_size + 1})"
            else:
                status = "🤔 Processing..."

            _render_async_status(display_id, request_id, status, cancellable=True)

            # Submit to queue
            request = CodexRequest(
                backend=active_backend,
                prompt=prompt,
                display_id=display_id,
                request_id=request_id,
                status_text=status,
                context_cells=context_cells,
                context_chars=context_chars,
                mode=mode,
                emit_events=emit_events,
            )
            if _CODEX_QUEUE is not None:
                _CODEX_QUEUE.put(request)
                _ASYNC_IDLE.clear()

            return None

        # Synchronous mode: execute immediately
        def _cancel_sync() -> None:
            _stop_session(
                agent=normalized,
                backend=session_backend,
                force=True,
                wait_for_tasks=False,
            )

        progress = _Progress(render=stream, _cancel=_cancel_sync)

        # Build prompt with proper order: template -> context -> user prompt
        session_backend = active_backend
        agent_name = getattr(session_backend, "name", "codex")
        parts = []

        # 1. Template (first turn only)
        if session_backend.first_turn():
            template = _resolve_template(agent_name)
            if template:
                _log_template(template)
                parts.append(template)

        # 2. Context (if enabled)
        if context_changes:
            context_block = _build_context_block(
                context_cells, context_chars, agent_name
            )
            if context_block:
                _log_context_block(context_block)
                parts.append(f"Context (changed cells):\n{context_block}")

        interagent_context = _build_interagent_context(active_agent_name)
        if interagent_context:
            parts.append(interagent_context)

        # 3. User prompt
        if parts:
            parts.append(f"User prompt:\n{prompt}")
            prompt = "\n\n".join(parts)

        _log_prompt(prompt)

        try:
            result, events = session_backend.send(
                prompt,
                on_event=_chain(progress.update, _log_event),
                on_approval=_prompt_approval,
            )
        except Exception as exc:  # pragma: no cover - surfaced to notebook
            if "pi backend" in str(exc).lower():
                msg = (
                    "❌ Claude support requires the pi CLI. Install it with:\n\n"
                    "`npm install -g @mariozechner/pi-coding-agent`\n\n"
                    "Then run `cleon.login('claude')` (or set `ANTHROPIC_API_KEY`) before retrying."
                )
                progress.finish(msg, markdown=True)
            else:
                progress.finish(f"❌ {exc}", markdown=False)
            return None

        # Extract response and log conversation (log full prompt with template + context)
        response = _extract_final_message(result)
        _log_conversation(prompt, response)
        _record_agent_history(active_agent_name, raw_user_prompt, response)

        if mode != "none":
            _display_result(result, mode, progress, agent_name)
        if emit_events:
            _print_events(events)
        return result if emit_events else None

    ip.register_magic_function(_codex_magic, magic_kind="cell", magic_name=normalized)
    # Register debug helper to inspect tracked context
    ip.register_magic_function(
        history_magic, magic_kind="cell", magic_name="cleon_history"
    )
    if auto:
        _enable_auto_route(ip, normalized, backend_name, agent_prefix)
    pretty_name = backend_name.capitalize()
    print(f"{pretty_name} session started.\n")
    if backend_name == "codex":
        help()
    return None  # type: ignore[return-value]


def register_codex_magic(**kwargs: Any) -> Callable[[str, str | None], Any]:
    """Convenience wrapper to register ``%%codex``."""

    return register_magic(name="codex", **kwargs)


def load_ipython_extension(ipython) -> None:
    """Hook for ``%load_ext cleon.magic``."""

    use(ipython=ipython)
    # Register custom history cell magic under a unique name to avoid clash with built-in %history
    ipython.register_magic_function(
        history_magic, magic_kind="cell", magic_name="cleon_history"
    )


def help() -> None:
    """Return concise help text without auto-displaying in notebooks."""

    text = """Add a `>` or `~` before your prompt to invoke Codex or Claude:
                 
> &gt; Whats your name?  
I am Codex!

~ Whats your name?
I'm Claude, Anthropic's AI assistant!
"""
    display(Markdown(text))


def stop(agent: str | None = None, *, force: bool = False) -> str | None:
    """Stop the shared cleon session and return the resume session id if known."""

    wait_for_tasks = not force
    agent_name = _default_agent_name(agent)
    backend = _BACKENDS.get(agent_name)
    if backend is None:
        print(f"No backend named '{agent_name}' is active.")
        return None
    session_id = _stop_session(
        agent=agent_name, backend=backend, force=force, wait_for_tasks=wait_for_tasks
    )
    if _ASYNC_MODE:
        if wait_for_tasks:
            _wait_for_async_tasks()
        _stop_worker_thread()
    return session_id


def status() -> dict[str, Any]:
    queued = _CODEX_QUEUE.qsize() if _CODEX_QUEUE is not None else 0
    backends_status = {}
    for name, backend in _BACKENDS.items():
        backends_status[name] = {
            "agent": getattr(backend, "name", name),
            "alive": backend.session_alive(),
        }
    runtime = {
        "active_agent": getattr(_ACTIVE_BACKEND, "name", None)
        if _ACTIVE_BACKEND
        else None,
        "async_mode": _ASYNC_MODE,
        "queued_requests": queued,
        "active_requests": _active_async_requests(),
        "backends": backends_status,
    }
    result = {
        "runtime": runtime,
        "settings": status_summary(),
        "sessions": _load_session_store(),
    }
    current_agent = runtime["active_agent"]
    current_agent_name: str | None = (
        current_agent if isinstance(current_agent, str) else None
    )
    alive = _session_alive(current_agent_name) if current_agent_name else False
    print(
        "cleon status - "
        f"agent: {current_agent or 'none'}, "
        f"session: {'alive' if alive else 'stopped'}, "
        f"async queued: {queued}, "
        f"in-flight: {runtime['active_requests']}"
    )
    return result


def resume(agent: str = "codex", session_id: str | None = None) -> str | None:
    """Resume a saved cleon session (defaults to current notebook entry)."""

    agent = _default_agent_name(agent)
    sid = session_id
    session_name = None
    timestamp = None
    if sid is None:
        store = _load_session_store()
        key = _get_notebook_name() or "default"
        chosen_entry = None
        direct_entry = store.get(key)
        if direct_entry and direct_entry.get("agent") == agent:
            chosen_entry = direct_entry
        else:
            fallback = store.get("_last")
            if fallback and fallback.get("agent") == agent:
                chosen_entry = fallback
        if chosen_entry:
            sid = chosen_entry.get("session_id")
            session_name = chosen_entry.get("notebook")
            timestamp = chosen_entry.get("timestamp")
    if not sid:
        print("No saved session to resume. Start a new one with cleon.use(...).")
        return None
    if session_name:
        human_time = ""
        if timestamp:
            try:
                human_time = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(float(timestamp))
                )
            except Exception:
                human_time = ""
        msg = f'Resuming session "{session_name}"'
        if human_time:
            msg += f" from {human_time}"
        print(msg)
    use(agent, agent=agent, session_id=sid)
    return sid


def reset() -> dict[str, Any]:
    """Stop running agents and reset cleon settings/session state."""

    for backend_name in list(_BACKENDS.keys()):
        stop(agent=backend_name, force=True)
    session_path = _session_store_path()
    if session_path.exists():
        try:
            session_path.unlink()
        except Exception:
            pass
    data = settings_reset()
    print("cleon reset complete. Default settings restored.")
    return data


def mode(name: str | None = None, *, agent: str | None = None) -> str:
    """Get or set the default mode for an agent."""

    target_agent = _default_agent_name(agent)
    if name is None:
        return get_default_mode(target_agent)
    settings_default_mode(name, agent=target_agent)
    _reset_first_turn(agent=target_agent)
    return name


def add_mode(
    name: str, template: str | None = None, *, agent: str | None = None
) -> dict[str, Any]:
    """Register a custom mode with an optional template."""

    normalized = name.strip().lower()
    return settings_add_mode(normalized, template, agent=agent)


def default_mode(name: str, *, agent: str | None = None) -> dict[str, Any]:
    """Explicitly set the default mode for an agent."""

    normalized = name.strip().lower()
    result = settings_default_mode(normalized, agent=_default_agent_name(agent))
    _reset_first_turn(agent=_default_agent_name(agent))
    return result


def _reset_first_turn(agent: str | None) -> None:
    """Mark active backends so the next turn re-sends the mode template."""
    target = _default_agent_name(agent)
    backend = _BACKENDS.get(target)
    if backend is None:
        return
    setter = getattr(backend, "reset_first_turn", None)
    if callable(setter):
        try:
            setter()
        except Exception:
            pass


def sessions() -> dict[str, dict[str, str]]:
    """Return stored session metadata."""

    store = _load_session_store()

    class _SessionView(dict):
        def __repr_html__(self) -> str:  # pragma: no cover - IPython display
            rows = []
            for name, meta in self.items():
                if name.startswith("_") and name != "_last":
                    continue
                session_id = meta.get("session_id", "")
                agent = meta.get("agent", "")
                notebook = meta.get("notebook", name)
                timestamp = meta.get("timestamp")
                human = ""
                if timestamp:
                    try:
                        human = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(float(timestamp))
                        )
                    except Exception:
                        human = ""
                rows.append(
                    f"<tr><td>{notebook}</td><td>{agent}</td><td>{session_id}</td><td>{human}</td></tr>"
                )
            if not rows:
                rows.append(
                    "<tr><td colspan='4' style='color:#888;'>No saved sessions.</td></tr>"
                )
            table = """
            <table style="border-collapse:collapse;margin-top:8px;">
                <thead>
                    <tr style="background:#222;color:#f5f5f5;">
                        <th style="padding:6px 10px;border:1px solid #444;">Notebook</th>
                        <th style="padding:6px 10px;border:1px solid #444;">Agent</th>
                        <th style="padding:6px 10px;border:1px solid #444;">Session ID</th>
                        <th style="padding:6px 10px;border:1px solid #444;">Saved</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
            """.format(rows="".join(rows))
            return table

    view = _SessionView(store)
    return view


def _record_agent_history(agent: str, prompt: str, response: str) -> None:
    if not response.strip():
        return
    entry = {
        "agent": agent,
        "prompt": prompt.strip(),
        "response": response.strip(),
        "timestamp": time.time(),
    }
    with _AGENT_HISTORY_LOCK:
        history_map = _ensure_history_map()
        history = history_map.setdefault(agent, deque(maxlen=_HISTORY_LIMIT))
        history.append(entry)


def _build_interagent_context(agent: str, limit: int = 4) -> str:
    with _AGENT_HISTORY_LOCK:
        history_map = _ensure_history_map()
        entries: list[dict[str, Any]] = []
        for other_agent, history in history_map.items():
            if other_agent == agent:
                continue
            entries.extend(list(history)[-limit:])
    entries = [entry for entry in entries if isinstance(entry, dict)]
    if not entries:
        return ""
    entries.sort(key=lambda e: e.get("timestamp", 0))
    blocks = ["Other agent updates:"]
    for entry in entries:
        prompt = entry.get("prompt", "")
        response = entry.get("response", "")
        blocks.append(
            f"Agent {entry['agent']} responded to:\n{prompt}\n\nTheir response:\n{response}"
        )
    return "\n\n".join(blocks)


def _ensure_history_map() -> dict[str, deque[dict[str, Any]]]:
    global _AGENT_HISTORY
    if isinstance(_AGENT_HISTORY, dict):
        return _AGENT_HISTORY
    converted: dict[str, deque[dict[str, Any]]] = {}
    if isinstance(_AGENT_HISTORY, list):
        for entry in _AGENT_HISTORY:
            if not isinstance(entry, dict):
                continue
            agent_name = entry.get("agent") or "codex"
            history = converted.setdefault(agent_name, deque(maxlen=_HISTORY_LIMIT))
            history.append(entry)
    else:
        converted["codex"] = deque(maxlen=_HISTORY_LIMIT)
    _AGENT_HISTORY = converted
    return converted


def _ensure_ipython(ipython) -> Any:
    ip = ipython or get_ipython()
    if ip is None:
        raise RuntimeError("No active IPython session; run inside Jupyter or IPython.")
    return ip


def _enable_auto_route(ip, magic_name: str, agent_name: str, prefix: str) -> None:
    """Intercept run_cell to auto-route natural language cells to cleon magics."""

    global _AUTO_ROUTE_INSTALLED, _ORIG_RUN_CELL
    if prefix:
        _AUTO_ROUTE_RULES[prefix] = (agent_name, magic_name)
    if _AUTO_ROUTE_INSTALLED:
        return
    orig = getattr(ip, "run_cell", None)
    if orig is None:
        return

    def _wrapped_run_cell(raw_cell, *args, **kwargs):
        try:
            text = raw_cell if isinstance(raw_cell, str) else ""
            detected = _detect_auto_route_target(text)
            if detected is not None:
                target_magic, prompt_text = detected
                routed_cell = f"%%{target_magic}\n{prompt_text}"
                return orig(routed_cell, *args, **kwargs)
        except Exception:
            pass  # On heuristic failure, fall back to normal execution
        return orig(raw_cell, *args, **kwargs)

    ip.run_cell = _wrapped_run_cell
    _ORIG_RUN_CELL = orig
    _AUTO_ROUTE_INSTALLED = True


def _detect_auto_route_target(raw_cell: str) -> tuple[str, str] | None:
    if not raw_cell:
        return None
    text = raw_cell.lstrip()
    if not text or text.startswith(("%%", "%", "!", "?")):
        return None
    for prefix, (_, magic_name) in _AUTO_ROUTE_RULES.items():
        if _cell_has_prefix(raw_cell, prefix):
            prompt_text = _strip_prompt_prefix(raw_cell, prefix)
            return magic_name, prompt_text
    return None


def _cell_has_prefix(raw_cell: str, prefix: str) -> bool:
    lines = raw_cell.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return False
    for ln in non_empty:
        if not ln.lstrip().startswith(prefix):
            return False
    return True


def _strip_prompt_prefix(text: str, prefix: str) -> str:
    lines = text.splitlines()
    stripped_lines = []
    for ln in lines:
        candidate = ln.lstrip()
        if candidate.startswith(prefix):
            cleaned = candidate[len(prefix) :]
            if cleaned.startswith(" "):
                cleaned = cleaned[1:]
            stripped_lines.append(cleaned)
        else:
            stripped_lines.append(ln)
    return "\n".join(stripped_lines)


def _normalize_payload(line: str, cell: str | None) -> str:
    payload = cell if cell is not None else line
    return payload.strip()


def _display_result(
    result: Any, mode: DisplayMode, progress: "_Progress", agent: str
) -> None:
    text = _extract_final_message(result)
    use_markdown: bool = bool(mode == "markdown" or (mode == "auto" and text))
    message = text or "(no final message)"
    themed_html = _render_agent_block(message, agent, markdown=use_markdown)
    progress.finish(themed_html, raw_html=True)
    progress.last_result_text = text or ""


def _extract_final_message(result: Any) -> str:
    if isinstance(result, Mapping):
        final = result.get("final_message")  # type: ignore[arg-type]
        if isinstance(final, str) and final.strip():
            return final
        summary = result.get("summary")  # type: ignore[arg-type]
        if isinstance(summary, str) and summary.strip():
            return summary
        # Provide a concise fallback instead of dumping the whole mapping
        errors = result.get("errors")  # type: ignore[arg-type]
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, str):
                return f"Error: {first}"
            if isinstance(first, Mapping) and "message" in first:
                msg = first.get("message")
                if isinstance(msg, str):
                    return f"Error: {msg}"
        status = result.get("status")  # type: ignore[arg-type]
        if isinstance(status, str) and status:
            return status
        # Agent message fallback
        msgs = result.get("events")  # type: ignore[arg-type]
        if isinstance(msgs, list):
            for ev in msgs:
                if isinstance(ev, Mapping):
                    item = ev.get("item")
                    if (
                        isinstance(item, Mapping)
                        and item.get("type") == "agent_message"
                    ):
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            return text
    if isinstance(result, str):
        return result
    return ""


def _agent_theme(agent: str) -> dict[str, str]:
    base = dict(_AGENT_THEMES.get(agent, _AGENT_THEMES["default"]))
    overrides = get_agent_theme(agent)
    if overrides:
        base.update(overrides)
    return base


def _render_agent_block(content: str, agent: str | None, *, markdown: bool) -> str:
    if plain_text_output():
        return (
            "<pre style='margin:0;white-space:pre-wrap;font-size:0.95em;"
            "background:transparent;color:inherit;font-family:SFMono-Regular,Consolas,"
            '"Liberation Mono",Menlo,monospace;\'>'
            f"{html.escape(content)}</pre>"
        )
    agent_name = agent or "codex"
    theme = _agent_theme(agent_name)
    inner = None
    if markdown:
        inner = _render_markdown_html(content)
        if inner is None:
            inner = _render_markdown_fallback(content)
    if not inner:
        inner = (
            f"<pre style='margin:0;white-space:pre-wrap;font-size:0.95em;background:transparent;color:inherit;'>"
            f"{html.escape(content)}</pre>"
        )
    inner = (
        f"<div style='background:transparent;color:inherit;font-size:0.95em;line-height:1.5;'>"
        f"{inner}</div>"
    )
    icon_html = ""
    icon_ref = _agent_icon(agent_name)
    if icon_ref:
        icon_html = icon_ref
    unique_class = f"cleon-bubble-{agent}"
    block = f"""
<div class="cleon-result {unique_class}" style="position:relative;margin-left:10px;padding:12px 32px 12px 16px;border-radius:12px;border:1px solid {theme["light_border"]};background:{theme["light_bg"]};color:{theme["light_color"]};box-shadow:0 8px 18px rgba(0,0,0,0.18);">
{icon_html}
{inner}
</div>
<style>
@media (prefers-color-scheme: dark) {{
    .{unique_class} {{
        background: {theme["dark_bg"]};
        border-color: {theme["dark_border"]};
        color: {theme["dark_color"]};
    }}
}}
</style>
"""
    return block


def _render_markdown_html(content: str) -> str | None:
    try:
        ip = get_ipython()
        if ip is None:
            raise RuntimeError
        formatter = ip.display_formatter
        data, _ = formatter.format(Markdown(content), include={"text/html"})
        html_output = data.get("text/html")
        if html_output:
            return html_output
    except Exception:
        pass
    return None


def _render_markdown_fallback(content: str) -> str:
    """Minimal Markdown-ish renderer for code fences and paragraphs."""
    lines = content.splitlines()
    parts: list[str] = []
    in_code = False
    code_lang = ""
    code_buf: list[str] = []

    def _flush_code() -> None:
        nonlocal code_buf
        if not code_buf:
            return
        code_text = "\n".join(code_buf)
        escaped = html.escape(code_text)
        lang_class = f"language-{html.escape(code_lang)}" if code_lang else ""
        parts.append(
            "<pre style='margin:8px 0 10px 0; padding:10px 12px; "
            "white-space:pre-wrap; background:rgba(0,0,0,0.08); "
            "border:1px solid rgba(255,255,255,0.08); border-radius:8px;"
            'font-family:SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace;'
            "font-size:0.95em;'>"
            f"<code class='{lang_class}'>{escaped}</code>"
            "</pre>"
        )
        code_buf = []

    for line in lines:
        if line.startswith("```"):
            fence_lang = line[3:].strip()
            if in_code:
                _flush_code()
                in_code = False
                code_lang = ""
            else:
                in_code = True
                code_lang = fence_lang
            continue
        if in_code:
            code_buf.append(line)
        else:
            if line.strip():
                parts.append(
                    "<p style='margin:0 0 6px 0; line-height:1.45;'>"
                    f"{html.escape(line)}</p>"
                )
            else:
                parts.append("<div style='height:6px'></div>")

    if in_code:
        _flush_code()

    return "".join(parts) or html.escape(content)


def _agent_icon(agent: str) -> str | None:
    rel = _AGENT_ICON_PATHS.get(agent)
    if not rel:
        return ""
    try:
        data = importlib_resources.files(__package__).joinpath(rel).read_bytes()
    except Exception:
        return ""
    encoded = base64.b64encode(data).decode("ascii")
    css = f"<style>.cleon-icon-{agent} {{ background-image:url('data:image/png;base64,{encoded}'); }}</style>"
    try:
        display(HTML(css))
    except Exception:
        pass
    return f"<span class='cleon-icon cleon-icon-{agent}'></span>"


def _print_events(events: Any) -> None:
    if isinstance(events, Iterable) and not isinstance(events, (str, bytes)):
        for idx, event in enumerate(events, start=1):
            print(f"Event {idx}: {event}")
    else:
        print(events)


class _Progress:
    def __init__(
        self,
        render: bool,
        _cancel: Callable[[], None] | None = None,
        display_id: str | None = None,
        initial_message: str | None = None,
        agent: str | None = None,
    ) -> None:
        self.handle = None
        self.handle_id = display_id if render else None
        self.last_message = initial_message or "Working..."
        self.last_result_text: str = ""
        self._stop = threading.Event()
        self._cancel = _cancel
        self._thread = (
            threading.Thread(target=self._loop, daemon=True) if render else None
        )
        self.agent = agent
        if self._thread is not None:
            self._thread.start()
        if render:
            if display_id is None:
                self.handle = display(HTML(""), display_id=True)
            else:
                update_display(HTML(initial_message or ""), display_id=display_id)
            if not self.last_message:
                self.last_message = "Working..."
            self.update_message(self.last_message, markdown=False)

    def _render_content(self, message: str) -> str:
        if self.agent:
            return _render_agent_block(message, self.agent, markdown=False)
        style = "color: #666; font-family: monospace;"
        return f'<div style="{style}">{message}</div>'

    def update(self, event: Any) -> None:
        msg = _summarize_event(event) or self.last_message
        self.last_message = msg
        rendered = HTML(self._render_content(msg))
        if self.handle_id is not None:
            update_display(rendered, display_id=self.handle_id)
            return
        if self.handle is not None:
            self.handle.update(rendered)

    def update_message(self, message: str, *, markdown: bool = False) -> None:
        self.last_message = message
        if markdown and not self.agent:
            rendered = Markdown(message)
        else:
            rendered = HTML(self._render_content(message))
        if self.handle_id is not None:
            update_display(rendered, display_id=self.handle_id)
            return
        if self.handle is not None:
            self.handle.update(rendered)

    def finish(
        self, message: str, markdown: bool = False, *, raw_html: bool = False
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        if raw_html:
            rendered = HTML(message)
        elif markdown:
            rendered = Markdown(message)
        else:
            rendered = HTML(self._render_content(message))
        if self.handle_id is not None:
            update_display(rendered, display_id=self.handle_id)
        elif self.handle is not None:
            self.handle.update(rendered)
        self.handle = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.handle is not None:
                msg = self.last_message
                if self.agent:
                    self.handle.update(
                        HTML(_render_agent_block(msg, self.agent, markdown=False))
                    )
                else:
                    self.handle.update(HTML(f"<code>{msg}</code>"))
            time.sleep(0.2)


def _summarize_event(event: Any) -> str:
    if isinstance(event, dict):
        etype = event.get("type")
        if etype:
            if etype == "token":
                token = event.get("text") or event.get("data") or ""
                return f"token: {str(token)[:40]}"
            if etype == "reasoning":
                text = event.get("text") or ""
                return f"reasoning: {str(text)[:80]}"
            if etype == "command_execution":
                cmd = event.get("command") or ""
                status = event.get("status") or "running"
                return f"command ({status}): {str(cmd)[:80]}"
            if "item" in event and isinstance(event["item"], Mapping):
                item = event["item"]
                item_type = item.get("type")
                if item_type == "reasoning":
                    return f"reasoning: {str(item.get('text', ''))[:80]}"
                if item_type == "command_execution":
                    cmd = item.get("command") or ""
                    status = item.get("status") or "running"
                    return f"command ({status}): {str(cmd)[:80]}"
                if item_type == "agent_message":
                    text = item.get("text") or ""
                    return f"agent: {str(text)[:80]}"
            # Surface interactive requests/approvals if present
            if etype in {"user_input.request", "ask_user_input", "ask.approval"}:
                prompt = event.get("prompt") or event.get("question") or ""
                return f"awaiting input: {str(prompt)[:80] or '…'}"
            if etype == "turn.result" and "result" in event:
                return "finalizing..."
            return str(etype)
    return ""


def _chain(
    first: Callable[[Any], None] | None, second: Callable[[Any], None]
) -> Callable[[Any], None]:
    def _inner(ev: Any) -> None:
        # Check for cancel request set by the notebook cancel button
        if getattr(__import__("builtins"), "window", None):
            pass  # placeholder to keep lint quiet for environments without window
        if first is not None:
            try:
                first(ev)
            except Exception:
                pass
        try:
            second(ev)
        except Exception:
            pass

    return _inner


def _configure_logging(path: str | os.PathLike[str] | None) -> None:
    global _LOG_PATH
    if path is None:
        return
    _LOG_PATH = str(path)
    Path(_LOG_PATH).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _configure_cancel(path: str | os.PathLike[str] | None) -> None:
    global _CANCEL_PATH
    _CANCEL_PATH = str(path) if path is not None else None


def _log_event(event: Any) -> None:
    if _LOG_PATH is None:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False))
            f.write("\n")
    except Exception:
        pass


def _log_events(events: Iterable[Any]) -> None:
    for ev in events:
        _log_event(ev)


def _log_prompt(prompt: str) -> None:
    if _LOG_PATH is None:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "prompt", "data": prompt}, ensure_ascii=False))
            f.write("\n")
    except Exception:
        pass


def _log_template(template: str) -> None:
    if _LOG_PATH is None:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"type": "template", "data": template}, ensure_ascii=False)
            )
            f.write("\n")
    except Exception:
        pass


def _resolve_template(agent: str) -> str | None:
    """Resolve template for agent via settings or local file fallback."""

    template = template_for_agent(agent)
    if template:
        return template
    return _load_template_file()


def _load_template_file() -> str | None:
    """Load template.md or prompts/learn.md from current working directory if it exists."""
    try:
        candidates = [
            Path.cwd() / "prompts" / "learn.md",
            Path.cwd() / "template.md",
        ]
        for template_path in candidates:
            if template_path.exists() and template_path.is_file():
                return template_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def _get_notebook_name() -> str | None:
    """Try to detect the current notebook filename."""
    try:
        ip = get_ipython()
        if ip is None:
            return None
        # Try to get notebook name from IPython
        if hasattr(ip, "user_ns") and "__vsc_ipynb_file__" in ip.user_ns:
            nb_path = ip.user_ns["__vsc_ipynb_file__"]
            return Path(nb_path).stem
        # Try Jupyter classic/lab
        from jupyter_client import find_connection_file  # type: ignore[import-not-found]

        find_connection_file()
        # This is a fallback - not perfect but works in many cases
        for nb_file in Path.cwd().glob("*.ipynb"):
            return nb_file.stem
    except Exception:
        pass
    return None


def _configure_conversation_log() -> None:
    """Set up conversation log based on notebook name."""
    global _CONVERSATION_LOG_PATH
    nb_name = _get_notebook_name()
    if nb_name:
        _CONVERSATION_LOG_PATH = str(Path.cwd() / f"{nb_name}.log")
        Path(_CONVERSATION_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)


def _log_conversation(prompt: str, response: str) -> None:
    """Log just the user prompt and assistant response to notebook-specific log."""
    if _CONVERSATION_LOG_PATH is None:
        return
    try:
        with Path(_CONVERSATION_LOG_PATH).open("a", encoding="utf-8") as f:
            f.write(f"{'=' * 80}\n")
            f.write(f"USER:\n{prompt}\n\n")
            f.write(f"ASSISTANT:\n{response}\n")
            f.write(f"{'=' * 80}\n\n")
    except Exception:
        pass


def _log_context_block(block: str) -> None:
    if _LOG_PATH is None or not block:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"type": "context.block", "data": block}, ensure_ascii=False)
            )
            f.write("\n")
    except Exception:
        pass


def _log_context_debug(payload: dict[str, Any]) -> None:
    if _LOG_PATH is None:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(
                json.dumps({"type": "context.debug", **payload}, ensure_ascii=False)
            )
            f.write("\n")
    except Exception:
        pass


def _maybe_prompt_followup(
    backend: AgentBackend, mode: DisplayMode, progress: "_Progress"
) -> None:
    # Heuristic: if last result text looks like a question or request, offer a reply
    text = progress.last_result_text.strip()
    if not text or "?" not in text:
        return
    reply = _prompt_user_input(text)
    if reply is None or not reply.strip():
        return
    reply = reply.strip()

    def _cancel() -> None:
        _stop_session(backend=backend, agent=agent, force=True, wait_for_tasks=False)

    resp_progress = _Progress(
        render=True if mode != "none" else False,
        _cancel=_cancel,
    )
    agent = getattr(backend, "name", "codex")
    try:
        result, events = backend.send(
            reply, on_event=_chain(resp_progress.update, _log_event)
        )
    except Exception as exc:
        print(f"Failed to send reply: {exc}")
        return
    _display_result(result, mode, resp_progress, agent)
    _print_events(events)
    _record_agent_history(agent, reply, _extract_final_message(result))
    return


def _prompt_user_input(question: str) -> str | None:
    """Display a blocking prompt in notebooks or fallback to stdin."""
    # Try a widget first
    try:
        import ipywidgets as widgets  # type: ignore
        from IPython.display import clear_output  # type: ignore

        prompt_blocks: list[widgets.Widget] = [
            widgets.HTML(
                value=f"<pre style='white-space: pre-wrap; font-size: 0.95em;'>{question}</pre>"
            )
        ]
        text = widgets.Text(
            placeholder="Type response…",
            description="codex:",
            layout=widgets.Layout(width="60%"),
        )
        button = widgets.Button(description="Send", button_style="primary")
        feedback = widgets.Output()

        completed = threading.Event()
        result: dict[str, str] = {"value": ""}

        def finish(_: object | None = None) -> None:
            result["value"] = text.value
            completed.set()

        button.on_click(finish)
        text.on_submit(finish)
        prompt_blocks.append(widgets.HBox([text, button]))
        prompt_blocks.append(feedback)
        display(widgets.VBox(prompt_blocks))

        while not completed.wait(0.05):
            pass

        with feedback:
            clear_output()
            print(f"Sent: {result['value']}")
        return result["value"]
    except Exception:
        pass

    # Fallback to stdin
    try:
        return input(
            f"\nAGENT REQUEST:\n> {question}\n↪ Reply (press Enter to skip): "
        ).strip()
    except Exception:
        return None


def _prompt_approval(event: dict[str, Any]) -> str | None:
    kind = event.get("kind", "approval")
    command = event.get("command")
    reason = event.get("reason")
    cwd = event.get("cwd")
    display_id = f"codex-approval-{uuid.uuid4().hex[:8]}"
    options = {
        "1": ("approve", "Approve"),
        "2": ("approve_session", "Approve for session"),
        "3": ("deny", "Deny"),
        "4": ("abort", "Abort task"),
    }
    question_lines = [f"Approval request ({kind})"]
    if command:
        question_lines.append(f"Command: {command}")
    if cwd:
        question_lines.append(f"cwd: {cwd}")
    if reason:
        question_lines.append(f"Reason: {reason}")
    question_text = "\n".join(question_lines)

    # Try widget UI first
    try:
        import ipywidgets as widgets  # type: ignore
        from IPython.display import clear_output  # type: ignore

        buttons: list[widgets.Button] = []
        choice: dict[str, str | None] = {"value": None}
        out = widgets.Output()

        def handler(decision: str, label: str):
            choice["value"] = decision
            with out:
                clear_output()
                print(f"Selected: {label}")
            for b in buttons:
                b.disabled = True

        for key, (decision, label) in options.items():
            btn = widgets.Button(description=f"{key}. {label}", button_style="primary")
            btn.on_click(lambda _b, d=decision, lbl=label: handler(d, lbl))
            buttons.append(btn)

        display(
            widgets.VBox(
                [
                    widgets.HTML(
                        value=f"<pre style='white-space: pre-wrap; font-size: 0.95em;'>{question_text}</pre>"
                    ),
                    widgets.HBox(buttons),
                    out,
                ]
            ),
            display_id=display_id,
        )

        # Wait until a button is clicked
        while choice["value"] is None:
            time.sleep(0.05)
        return str(choice["value"])
    except Exception:
        pass

    # Fallback to stdin
    try:
        update_display(
            Markdown(f"**Approval requested**\n\n```\n{question_text}\n```"),
            display_id=display_id,
        )
    except Exception:
        pass
    print(question_text)
    for key, (_, label) in options.items():
        print(f"{key}. {label}")
    try:
        selection = input("Select option (1-4) or Enter to skip: ").strip()
    except Exception:
        return None
    if not selection:
        return None
    if selection in options:
        return options[selection][0]
    return selection


def _stop_session(
    *,
    agent: str | None = None,
    backend: AgentBackend | None = None,
    keep_backend: bool = False,
    force: bool = False,
    wait_for_tasks: bool = True,
) -> str | None:
    global _ACTIVE_BACKEND, _ACTIVE_BACKEND_NAME
    target_backend = backend
    backend_name = agent
    if target_backend is None:
        name = agent or _ACTIVE_BACKEND_NAME or "codex"
        backend_name = name
        target_backend = _BACKENDS.get(name)
    if target_backend is None:
        return None
    if force:
        _cancel_all()
    elif wait_for_tasks:
        _wait_for_async_tasks()
    info = target_backend.stop()
    session_id = info.session_id
    resume_cmd = info.resume_command
    if (
        session_id
        and backend_name in {"codex", None}
        and getattr(target_backend, "name", "") == "codex"
    ):
        if not resume_cmd:
            resume_cmd = f"cleon --resume {session_id}"
        _persist_session_id(session_id, resume_cmd, target_backend.name)
        print(
            "Session stopped.\n"
            "You can resume with:\n"
            f'  cleon.use("{target_backend.name}", session_id="{session_id}")\n'
            "or\n"
            "  cleon.resume()"
        )
    if not keep_backend and backend_name and backend_name in _BACKENDS:
        if _ACTIVE_BACKEND_NAME == backend_name:
            _ACTIVE_BACKEND_NAME = None
            _ACTIVE_BACKEND = None
    return session_id


def _session_alive(agent: str | None = None) -> bool:
    backend = _BACKENDS.get(_default_agent_name(agent)) if agent else _ACTIVE_BACKEND
    if backend is None:
        return False
    return backend.session_alive()


class ContextTracker:
    def __init__(self) -> None:
        self.last_seen_map: dict[str, int] = {}
        self._baseline = 0

    def build_block(
        self,
        max_cells: int | None,
        max_chars: int | None,
        agent: str | None,
        peek: bool = False,
    ) -> str:
        ip = get_ipython()
        if ip is None:
            return ""
        history = ip.user_ns.get("In", [])
        outputs = ip.user_ns.get("Out", {})
        if not isinstance(history, list):
            return ""
        agent_key = agent or "_default"
        baseline = len(history) - 1

        # Sliding window mode: if max_cells is set, always get last N cells (not just new ones)
        # This ensures Codex always has context even on consecutive %%codex calls
        if max_cells is not None and max_cells > 0:
            # Start from the beginning or max_cells back, whichever is more recent
            start_idx = max(1, len(history) - max_cells)
        else:
            # Incremental mode: only new cells since last_seen
            last_seen = self.last_seen_map.get(agent_key, self._baseline)
            start_idx = max(1, last_seen + 1)

        cells = []
        for idx in range(start_idx, len(history)):
            src = history[idx]
            if not isinstance(src, str):
                continue
            text = src.strip()
            # Skip %%codex, %%cleon_history, and line magics (both in magic form and IPython internal form)
            if (
                text.startswith("%%codex")
                or text.startswith("%%cleon_history")
                or text.startswith("%")  # Skip all line magics like %history
                or "run_cell_magic('codex'" in text
                or 'run_cell_magic("codex"' in text
                or "run_cell_magic('cleon_history'" in text
                or 'run_cell_magic("cleon_history"' in text
                or "run_line_magic(" in text
            ):  # Skip line magic internal calls
                continue
            code_block = (
                text
                if max_chars is None or len(text) <= max_chars
                else text[:max_chars] + "\n... [truncated]"
            )
            out_obj = outputs.get(idx) if isinstance(outputs, dict) else None
            out_text = ""
            if out_obj is not None:
                try:
                    out_text = str(out_obj)
                except Exception:
                    out_text = repr(out_obj)
                if max_chars is not None and len(out_text) > max_chars:
                    out_text = out_text[:max_chars] + "\n... [truncated]"
            cells.append((idx, code_block, out_text))

        # Apply max_cells limit if in incremental mode
        if max_cells is not None and max_cells > 0:
            cells = cells[-max_cells:]

        debug_info = {
            "start_idx": start_idx,
            "last_seen": self.last_seen_map.get(agent_key, self._baseline),
            "history_len": len(history) - 1,
            "cells_considered": [
                {"idx": idx, "has_output": bool(out), "code_len": len(code)}
                for idx, code, out in cells
            ],
            "peek": peek,
            "sliding_window": max_cells is not None and max_cells > 0,
            "agent": agent_key,
        }
        _log_context_debug(debug_info)
        if not cells:
            if not peek:
                self.last_seen_map[agent_key] = baseline
            return ""
        if not peek:
            self.last_seen_map[agent_key] = baseline
        parts = []
        for idx, code_block, out_text in cells:
            segment = [f"[cell {idx}]", "code:", code_block]
            if out_text:
                segment.append("output:")
                segment.append(out_text)
            parts.append("\n".join(segment))
        return "\n\n".join(parts)


def _configure_context() -> None:
    global _CONTEXT_TRACKER
    if _CONTEXT_TRACKER is None:
        _CONTEXT_TRACKER = ContextTracker()
        # Start tracking from current history position (ignore cells before cleon.use())
        ip = get_ipython()
        if ip is not None:
            history = ip.user_ns.get("In", [])
            if isinstance(history, list):
                _CONTEXT_TRACKER._baseline = len(history) - 1
                _CONTEXT_TRACKER.last_seen_map["_default"] = _CONTEXT_TRACKER._baseline


def _build_context_block(
    max_cells: int | None, max_chars: int | None, agent: str | None, peek: bool = False
) -> str:
    if _CONTEXT_TRACKER is None:
        return ""
    with _CONTEXT_LOCK:
        return _CONTEXT_TRACKER.build_block(max_cells, max_chars, agent, peek=peek)


def history_magic(line: str, cell: str | None = None) -> str | None:
    """Cell magic to display changed notebook history since last context build."""
    max_cells = None
    max_chars = None
    if line:
        parts = line.strip().split()
        if len(parts) >= 1:
            try:
                max_cells = int(parts[0])
            except Exception:
                max_cells = None
        if len(parts) >= 2:
            try:
                max_chars = int(parts[1])
            except Exception:
                max_chars = None
    block = _build_context_block(max_cells, max_chars, None, peek=True)
    if block:
        print("Changed cells since last Codex turn:\n")
        print(block)
    else:
        print("No changed cells detected.")
    return block
