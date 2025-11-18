"""IPython cell magic helpers for cleon."""

from __future__ import annotations

import json
import itertools
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import select
from typing import Any, Callable, Iterable, Mapping
import importlib.resources as importlib_resources

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


from ._cleon import run as cleon_run  # type: ignore[import-not-found]

DisplayMode = str
_SESSION: "SharedSession | None" = None
_LOG_PATH: str | None = None
_CONVERSATION_LOG_PATH: str | None = None
_CANCEL_PATH: str | None = None
_CONTEXT_TRACKER: "ContextTracker | None" = None
_ASYNC_MODE: bool = False
_CODEX_QUEUE: "queue.Queue[CodexRequest | None] | None" = None
_WORKER_THREAD: threading.Thread | None = None
_SESSION_LOCK = threading.Lock()
_AUTO_ROUTE_INSTALLED = False
_ORIG_RUN_CELL = None
_CANCELLED_REQUESTS: set[str] = set()
_CANCELLED_LOCK = threading.Lock()
_CANCEL_ALL = threading.Event()
_PENDING_REQUESTS: dict[str, str] = {}
_SESSION_ALLOWED = False


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
    runtime: Mapping[str, Any]


class SharedSession:
    """Lightweight persistent codex CLI process for multi-turn use."""

    def __init__(
        self,
        binary: str,
        env: Mapping[str, str] | None = None,
        session_id: str | None = None,
    ) -> None:
        self.binary = binary
        self.env = dict(env or {})
        self.proc: subprocess.Popen[str] | None = None
        self.first_turn: bool = True
        self.session_id: str | None = session_id
        self.rollout_path: str | None = None
        self.resume_command: str | None = None
        self.stopped: bool = False

    def ensure_started(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        cmd = [self.binary, "--json-events", "--json-result"]
        if self.session_id:
            cmd.extend(["--resume", self.session_id])
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **self.env},
            bufsize=1,
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                if self.proc.stdin:
                    try:
                        self.proc.stdin.write("__CLEON_STOP__\n")
                        self.proc.stdin.flush()
                    except Exception:
                        pass
                deadline = time.time() + 5.0
                while time.time() < deadline:
                    if self.proc.poll() is not None:
                        break
                    self._drain_stdout(capture_metadata=True)
                    time.sleep(0.05)
                self._drain_stdout(capture_metadata=True)
                if self.proc.poll() is None:
                    self.proc.terminate()
                    self._drain_stdout(capture_metadata=True)
            except Exception:
                try:
                    self._drain_stdout(capture_metadata=True)
                except Exception:
                    pass
                self.proc.kill()
        self.proc = None
        self.first_turn = True
        self.stopped = True

    def _read_lines(self) -> Iterable[str]:
        assert self.proc is not None
        if self.proc.stdout is None:
            return
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                break
            yield line.strip()

    def send(
        self,
        prompt: str,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]:
        with _SESSION_LOCK:
            self.ensure_started()
            assert self.proc is not None
            # Drain any leftover stdout before sending a new prompt
            self._drain_stdout(capture_metadata=True)
            if self.proc.stdin is None:
                raise RuntimeError("cleon session stdin unavailable")
            # Interactive mode uses read_line() which stops at first \n
            # Replace newlines with special marker so entire prompt is on one line
            single_line_prompt = prompt.replace("\n", " ⏎ ")
            self.proc.stdin.write(single_line_prompt + "\n")
            self.proc.stdin.flush()
            # Mark that we've sent at least one turn
            self.first_turn = False

            events: list[Any] = []
            final: Any | None = None
            for line in self._read_lines():
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                self._capture_session_metadata(parsed)
                events.append(parsed)
                if parsed.get("type") == "approval.request":
                    if on_approval is not None:
                        decision = on_approval(parsed)
                        if decision:
                            if self.proc.stdin is None:
                                raise RuntimeError("cleon session stdin unavailable")
                                # pragma: no cover
                            self.proc.stdin.write(decision + "\n")
                            self.proc.stdin.flush()
                            continue
                if on_event is not None:
                    try:
                        on_event(parsed)
                    except Exception:
                        pass
                if (
                    isinstance(parsed, dict)
                    and parsed.get("type") == "turn.result"
                    and "result" in parsed
                ):
                    final = parsed["result"]
                    break

            # Drain any trailing output the process may emit after turn.result
            self._drain_stdout()
            # Give the process a moment to flush any final output
            time.sleep(0.1)
            # Drain one more time to be sure
            self._drain_stdout()

            if final is None:
                raise RuntimeError("cleon output missing turn.result payload")
            return final, events

    def _drain_stdout(self, capture_metadata: bool = False) -> None:
        """Best-effort drain of any pending stdout to avoid bleed between turns."""
        assert self.proc is not None
        stdout = self.proc.stdout
        if stdout is None:
            return

        def _maybe_parse(line: str) -> None:
            if not capture_metadata:
                return
            try:
                parsed = json.loads(line)
                self._capture_session_metadata(parsed)
            except Exception:
                pass

        try:
            fd = stdout.fileno()
        except Exception:
            # Some file-like objects (e.g. StringIO in tests) don't expose fileno
            try:
                if hasattr(stdout, "seekable") and stdout.seekable():
                    for _ in range(50):
                        line = stdout.readline()
                        if not line:
                            break
                        _maybe_parse(line)
            except Exception:
                pass
            return

        for _ in range(50):
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            line = stdout.readline()
            if not line:
                break
            _maybe_parse(line)

    def _capture_session_metadata(self, payload: Any) -> None:
        """Pull session_id/rollout_path from json payloads when present."""
        try:
            if isinstance(payload, dict):
                if payload.get("type") == "session.resume":
                    if isinstance(payload.get("session_id"), str):
                        self.session_id = payload["session_id"]
                    if isinstance(payload.get("resume_command"), str):
                        self.resume_command = payload["resume_command"]
                    if isinstance(payload.get("rollout_path"), str):
                        self.rollout_path = payload["rollout_path"]
                if self.session_id is None:
                    if "session_id" in payload and isinstance(
                        payload["session_id"], str
                    ):
                        self.session_id = payload["session_id"]
                    elif (
                        "msg" in payload
                        and isinstance(payload["msg"], dict)
                        and isinstance(payload["msg"].get("session_id"), str)
                    ):
                        self.session_id = payload["msg"]["session_id"]
                if self.rollout_path is None:
                    if "rollout_path" in payload and isinstance(
                        payload["rollout_path"], str
                    ):
                        self.rollout_path = payload["rollout_path"]
                    elif (
                        "msg" in payload
                        and isinstance(payload["msg"], dict)
                        and isinstance(payload["msg"].get("rollout_path"), str)
                    ):
                        self.rollout_path = payload["msg"]["rollout_path"]
        except Exception:
            pass


def use(
    name: str = "codex",
    *,
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

    global _SESSION_ALLOWED
    _SESSION_ALLOWED = True
    register_magic(
        name=name,
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
            _process_codex_request(request)
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


def _reset_cancellations() -> None:
    _CANCEL_ALL.clear()
    with _CANCELLED_LOCK:
        _CANCELLED_REQUESTS.clear()
        _PENDING_REQUESTS.clear()
    _start_worker_thread()


def _session_store_path() -> Path:
    return Path.cwd() / ".cleon_session.json"


def _load_session_store() -> dict[str, dict[str, str]]:
    try:
        path = _session_store_path()
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _persist_session_id(session_id: str, resume_cmd: str | None) -> None:
    try:
        store = _load_session_store()
        key = _get_notebook_name() or "default"
        store[key] = {
            "session_id": session_id,
            "resume_command": resume_cmd or "",
            "agent": "codex",
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

    def _cancel() -> None:
        _stop_session()

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
        session = _shared_session(request.runtime)
        parts = []

        # 1. Template (first turn only)
        if session.first_turn:
            template = _load_template()
            if template:
                _log_template(template)
                parts.append(template)

        # 2. Context (if enabled)
        if _CONTEXT_TRACKER is not None:
            context_block = _build_context_block(
                request.context_cells, request.context_chars
            )
            if context_block:
                _log_context_block(context_block)
                parts.append(f"Context (changed cells):\n{context_block}")

        # 3. User prompt
        if parts:
            parts.append(f"User prompt:\n{request.prompt}")
            full_prompt = "\n\n".join(parts)
        else:
            full_prompt = request.prompt

        _log_prompt(full_prompt)

        # Send to codex
        result, events = session.send(
            full_prompt,
            on_event=_chain(progress.update, _log_event),
            on_approval=_prompt_approval,
        )

        # Extract response and log
        response = _extract_final_message(result)
        _log_conversation(full_prompt, response)

        # Update cell display with result
        if request.mode != "none":
            _display_result(result, request.mode, progress)

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

    runtime = _ensure_cleon_runtime(binary=binary, extra_env=env, session_id=session_id)

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

        # Command prefixes for mode control
        if prompt.startswith("/"):
            cmd, _, rest = prompt.partition(" ")
            cmd = cmd.lower()

            def _cancel() -> None:
                _stop_session()

            progress = _Progress(render=stream, _cancel=_cancel)

            # One-shot prompt (fresh process)
            if cmd in {"/fresh", "/once"}:
                payload = rest.strip()
                if not payload:
                    print("Usage: /fresh <prompt>")
                    return None
                result, events = cleon_run(payload)
                _log_events(events)
                if mode != "none":
                    _display_result(result, mode, progress)
                if emit_events:
                    _print_events(events)
                return result if emit_events else None

            if cmd == "/stop":
                _stop_session()
                if async_mode:
                    _stop_worker_thread()
                    print("cleon session and async worker stopped.")
                else:
                    print("cleon session stopped.")
                return None

            if cmd == "/status":
                alive = _session_alive()
                print(f"cleon session: {'running' if alive else 'stopped'}")
                return alive

            if cmd == "/new":
                _stop_session()
                result, events = _shared_session(runtime).send(
                    rest.strip(),
                    on_event=_chain(progress.update, _log_event),
                    on_approval=_prompt_approval,
                )
                if mode != "none":
                    _display_result(result, mode, progress)
                if emit_events:
                    _print_events(events)
                return result if emit_events else None

            if cmd == "/peek_history":
                if not context_changes:
                    print(
                        "Context tracking not enabled. Use cleon.use(..., context_changes=True)"
                    )
                    return None
                block = _build_context_block(context_cells, context_chars, peek=True)
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
                prompt=prompt,
                display_id=display_id,
                request_id=request_id,
                status_text=status,
                context_cells=context_cells,
                context_chars=context_chars,
                mode=mode,
                emit_events=emit_events,
                runtime=runtime,
            )
            if _CODEX_QUEUE is not None:
                _CODEX_QUEUE.put(request)

            return None

        # Synchronous mode: execute immediately
        def _cancel_sync() -> None:
            _stop_session()

        progress = _Progress(render=stream, _cancel=_cancel_sync)

        # Build prompt with proper order: template -> context -> user prompt
        session = _shared_session(runtime)
        parts = []

        # 1. Template (first turn only)
        if session.first_turn:
            template = _load_template()
            if template:
                _log_template(template)
                parts.append(template)

        # 2. Context (if enabled)
        if context_changes:
            context_block = _build_context_block(context_cells, context_chars)
            if context_block:
                _log_context_block(context_block)
                parts.append(f"Context (changed cells):\n{context_block}")

        # 3. User prompt
        if parts:
            parts.append(f"User prompt:\n{prompt}")
            prompt = "\n\n".join(parts)

        _log_prompt(prompt)

        try:
            result, events = session.send(
                prompt,
                on_event=_chain(progress.update, _log_event),
                on_approval=_prompt_approval,
            )
        except Exception as exc:  # pragma: no cover - surfaced to notebook
            print(f"cleon failed: {exc}")
            raise

        # Extract response and log conversation (log full prompt with template + context)
        response = _extract_final_message(result)
        _log_conversation(prompt, response)

        if mode != "none":
            _display_result(result, mode, progress)
        if emit_events:
            _print_events(events)
        return result if emit_events else None

    ip.register_magic_function(_codex_magic, magic_kind="cell", magic_name=normalized)
    # Register debug helper to inspect tracked context
    ip.register_magic_function(
        history_magic, magic_kind="cell", magic_name="cleon_history"
    )
    if auto:
        _enable_auto_route(ip, normalized)
    print("Codex session started.\n")
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

    text = """Add a `>` before your prompt:  
                 
> &gt; Whats your name?  
I am Codex!                   
"""
    display(Markdown(text))


def stop() -> str | None:
    """Stop the shared cleon session and return the resume session id if known."""

    session_id = _stop_session()
    if _ASYNC_MODE:
        _stop_worker_thread()
    return session_id


def resume(agent: str = "codex", session_id: str | None = None) -> str | None:
    """Resume a saved cleon session (defaults to current notebook entry)."""

    sid = session_id
    if sid is None:
        store = _load_session_store()
        key = _get_notebook_name() or "default"
        entry = store.get(key) or store.get("default")
        if entry and entry.get("agent") == agent:
            sid = entry.get("session_id")
    if not sid:
        print("No saved session to resume. Start a new one with cleon.use(...).")
        return None
    use(agent, session_id=sid)
    return sid


def _ensure_ipython(ipython) -> Any:
    ip = ipython or get_ipython()
    if ip is None:
        raise RuntimeError("No active IPython session; run inside Jupyter or IPython.")
    return ip


def _enable_auto_route(ip, magic_name: str) -> None:
    """Intercept run_cell to auto-route natural language cells to codex cell magic."""

    global _AUTO_ROUTE_INSTALLED, _ORIG_RUN_CELL
    if _AUTO_ROUTE_INSTALLED:
        return
    orig = getattr(ip, "run_cell", None)
    if orig is None:
        return

    def _wrapped_run_cell(raw_cell, *args, **kwargs):
        try:
            text = raw_cell if isinstance(raw_cell, str) else ""
            if _should_route_to_codex(text):
                prompt_text = _strip_prompt_prefix(text)
                routed_cell = f"%%{magic_name}\n{prompt_text}"
                return orig(routed_cell, *args, **kwargs)
        except Exception:
            pass  # On heuristic failure, fall back to normal execution
        return orig(raw_cell, *args, **kwargs)

    ip.run_cell = _wrapped_run_cell
    _ORIG_RUN_CELL = orig
    _AUTO_ROUTE_INSTALLED = True


def _should_route_to_codex(raw_cell: str) -> bool:
    """Return True only when the cell is a prefixed prompt (lines starting with '>')."""

    if not raw_cell:
        return False
    text = raw_cell.lstrip()
    if not text:
        return False
    if text.startswith(("%%", "%", "!", "?")):
        return False  # Respect explicit magics/shell/inspect

    lines = raw_cell.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    if non_empty and all(ln.lstrip().startswith(">") for ln in non_empty):
        return True
    return False


def _strip_prompt_prefix(text: str) -> str:
    """If all non-empty lines start with '> ', strip that prefix for Codex prompts."""

    lines = text.splitlines()
    non_empty = [ln for ln in lines if ln.strip()]
    if non_empty and all(ln.lstrip().startswith(">") for ln in non_empty):
        stripped_lines = []
        for ln in lines:
            if ln.lstrip().startswith(">"):
                # Remove leading ">" and one following space if present
                candidate = ln.lstrip()[1:]
                if candidate.startswith(" "):
                    candidate = candidate[1:]
                stripped_lines.append(candidate)
            else:
                stripped_lines.append(ln)
        return "\n".join(stripped_lines)
    return text


def _normalize_payload(line: str, cell: str | None) -> str:
    payload = cell if cell is not None else line
    return payload.strip()


def _display_result(result: Any, mode: DisplayMode, progress: "_Progress") -> None:
    text = _extract_final_message(result)
    if mode == "text":
        progress.finish(text or "(no final message)")
        return

    if mode == "markdown" or (mode == "auto" and text):
        progress.finish(text or "(no final message)", markdown=True)
    else:
        # In non-markdown mode, still avoid dumping raw JSON; show best-effort message.
        progress.finish(text or "(no final message)", markdown=False)
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


def _print_events(events: Any) -> None:
    if isinstance(events, Iterable) and not isinstance(events, (str, bytes)):
        for idx, event in enumerate(events, start=1):
            print(f"Event {idx}: {event}")
    else:
        print(events)


class _Progress:
    spinner = itertools.cycle("-\\|/")

    def __init__(
        self,
        render: bool,
        _cancel: Callable[[], None] | None = None,
        display_id: str | None = None,
        initial_message: str | None = None,
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
        if self._thread is not None:
            self._thread.start()
        if render:
            if display_id is None:
                self.handle = display(HTML(""), display_id=True)
            else:
                update_display(HTML(initial_message or ""), display_id=display_id)
            if initial_message:
                self.update_message(initial_message)

    def _render_content(self, message: str, spinner: bool = False) -> str:
        """Render content with optional spinner."""
        style = "color: #666; font-family: monospace;"
        return f'<div style="{style}">{message}</div>'

    def update(self, event: Any) -> None:
        msg = _summarize_event(event) or self.last_message
        self.last_message = msg
        rendered = HTML(
            self._render_content(f"{next(self.spinner)} {msg}", spinner=True)
        )
        if self.handle_id is not None:
            update_display(rendered, display_id=self.handle_id)
            return
        if self.handle is not None:
            self.handle.update(rendered)

    def update_message(self, message: str, *, markdown: bool = False) -> None:
        self.last_message = message
        rendered = (
            Markdown(message)
            if markdown
            else HTML(self._render_content(message, spinner=False))
        )
        if self.handle_id is not None:
            update_display(rendered, display_id=self.handle_id)
            return
        if self.handle is not None:
            self.handle.update(rendered)

    def finish(self, message: str, markdown: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        rendered = (
            Markdown(message)
            if markdown
            else HTML(self._render_content(message, spinner=False))
        )
        if self.handle_id is not None:
            update_display(rendered, display_id=self.handle_id)
        elif self.handle is not None:
            self.handle.update(rendered)
        self.handle = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.handle is not None:
                msg = self.last_message
                self.handle.update(HTML(f"<code>{next(self.spinner)} {msg}</code>"))
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


def _ensure_cleon_runtime(
    *,
    binary: str | None,
    extra_env: Mapping[str, str] | None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the codex CLI path and mutate process env accordingly."""

    runtime_env: dict[str, str] = {}
    if extra_env:
        for key, value in extra_env.items():
            os.environ[str(key)] = str(value)
            runtime_env[str(key)] = str(value)

    resolved = _resolve_cleon_binary(binary)
    if resolved is None:
        raise RuntimeError(
            "Could not find the 'cleon' CLI.\n"
            "Make sure it is on PATH, set $CLEON_BIN, or call cleon.use(..., binary='/path/to/cleon')."
        )
    os.environ["CLEON_BIN"] = resolved
    runtime_env["CLEON_BIN"] = resolved
    return {"binary": resolved, "env": runtime_env, "session_id": session_id}


def _resolve_cleon_binary(explicit: str | None) -> str | None:
    """Return a usable cleon binary path if available."""

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)

    env_value = os.environ.get("CLEON_BIN")
    if env_value:
        candidates.append(env_value)

    # Bundled binary inside the wheel (src/cleon/bin)
    try:
        pkg_bin = importlib_resources.files(__package__).joinpath("bin")
        for name in ("cleon.exe", "cleon"):
            cand = pkg_bin / name
            if cand.is_file():
                candidates.append(str(cand))
    except Exception:
        pass

    which_value = shutil.which("cleon")
    if which_value:
        candidates.append(which_value)

    # Heuristic: search upwards for a workspace target/{release,debug}/cleon
    for parent in Path(__file__).resolve().parents:
        target_dir = parent / "target"
        if not target_dir.exists():
            continue
        for profile in ("release", "debug"):
            candidate_path = target_dir / profile / "cleon"
            if candidate_path.is_file():
                candidates.append(str(candidate_path))

    seen: set[str] = set()
    for candidate in candidates:
        norm = os.path.expanduser(candidate)
        if norm in seen:
            continue
        seen.add(norm)
        path = Path(norm)
        if path.is_file():
            return str(path)

    return None


def _shared_session(runtime: Mapping[str, Any]) -> SharedSession:
    global _SESSION
    if not _SESSION_ALLOWED:
        raise RuntimeError(
            "cleon session not active. To resume call cleon.resume() or cleon.use(..., session_id=...) or start a new session."
        )
    if _SESSION is None:
        _SESSION = SharedSession(
            binary=str(runtime["binary"]),
            env=runtime.get("env") or {},
            session_id=runtime.get("session_id"),
        )
    if _SESSION.proc is None and _SESSION.stopped:
        msg = "cleon session stopped. Start a new session with cleon.use(..., session_id=...) to resume or cleon.use(...) to start fresh."
        raise RuntimeError(msg)
    return _SESSION


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


def _load_template() -> str | None:
    """Load template.md from current working directory if it exists."""
    try:
        template_path = Path.cwd() / "template.md"
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
    runtime: Mapping[str, Any], mode: DisplayMode, progress: "_Progress"
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
        _stop_session()

    resp_progress = _Progress(
        render=True if mode != "none" else False,
        _cancel=_cancel,
    )
    try:
        result, events = _shared_session(runtime).send(
            reply, on_event=_chain(resp_progress.update, _log_event)
        )
    except Exception as exc:
        print(f"Failed to send reply: {exc}")
        return
    _display_result(result, mode, resp_progress)
    _print_events(events)
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


def _stop_session() -> str | None:
    global _SESSION, _SESSION_ALLOWED
    if _SESSION is None:
        return None
    _SESSION.stop()
    session_id = _SESSION.session_id
    resume_cmd = _SESSION.resume_command
    if session_id:
        if not resume_cmd:
            resume_cmd = f"cleon --resume {session_id}"
        _persist_session_id(session_id, resume_cmd)
        print(
            "Session stopped.\n"
            "You can resume with:\n"
            f'  cleon.use("codex", session_id="{session_id}")\n'
            "or\n"
            "  cleon.resume()"
        )
    _SESSION = None
    _SESSION_ALLOWED = False
    return session_id


def _session_alive() -> bool:
    if _SESSION is None:
        return False
    proc = _SESSION.proc
    return proc is not None and proc.poll() is None


class ContextTracker:
    def __init__(self) -> None:
        self.last_seen: int = 0

    def build_block(
        self, max_cells: int | None, max_chars: int | None, peek: bool = False
    ) -> str:
        ip = get_ipython()
        if ip is None:
            return ""
        history = ip.user_ns.get("In", [])
        outputs = ip.user_ns.get("Out", {})
        if not isinstance(history, list):
            return ""

        # Sliding window mode: if max_cells is set, always get last N cells (not just new ones)
        # This ensures Codex always has context even on consecutive %%codex calls
        if max_cells is not None and max_cells > 0:
            # Start from the beginning or max_cells back, whichever is more recent
            start_idx = max(1, len(history) - max_cells)
        else:
            # Incremental mode: only new cells since last_seen
            start_idx = max(1, self.last_seen + 1)

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
            "last_seen": self.last_seen,
            "history_len": len(history) - 1,
            "cells_considered": [
                {"idx": idx, "has_output": bool(out), "code_len": len(code)}
                for idx, code, out in cells
            ],
            "peek": peek,
            "sliding_window": max_cells is not None and max_cells > 0,
        }
        _log_context_debug(debug_info)
        if not cells:
            if not peek:
                self.last_seen = len(history) - 1
            return ""
        if not peek:
            self.last_seen = len(history) - 1
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
                _CONTEXT_TRACKER.last_seen = len(history) - 1


def _build_context_block(
    max_cells: int | None, max_chars: int | None, peek: bool = False
) -> str:
    if _CONTEXT_TRACKER is None:
        return ""
    return _CONTEXT_TRACKER.build_block(max_cells, max_chars, peek=peek)


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
    block = _build_context_block(max_cells, max_chars, peek=True)
    if block:
        print("Changed cells since last Codex turn:\n")
        print(block)
    else:
        print("No changed cells detected.")
    return block
