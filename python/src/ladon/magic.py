"""IPython cell magic helpers for ladon."""

from __future__ import annotations

import json
import itertools
import os
import threading
import time
from pathlib import Path
import shutil
import subprocess
import select
from typing import Any, Callable, Iterable, Mapping

try:  # pragma: no cover - optional import when IPython is available
    from IPython import get_ipython  # type: ignore
    from IPython.display import Markdown, display, HTML  # type: ignore
except Exception:  # pragma: no cover - fallback when IPython is missing

    def get_ipython():  # type: ignore
        return None

    def display(*_: object, **__: object) -> None:  # type: ignore
        pass

    class HTML:  # type: ignore
        def __init__(self, data: str) -> None:
            self.data = data

    class Markdown:  # type: ignore
        def __init__(self, data: str) -> None:
            self.data = data


from ._ladon import run as ladon_run

DisplayMode = str
_SESSION: "SharedSession | None" = None
_LOG_PATH: str | None = None
_CONVERSATION_LOG_PATH: str | None = None
_CANCEL_PATH: str | None = None
_CONTEXT_TRACKER: "ContextTracker | None" = None


class SharedSession:
    """Lightweight persistent ladon CLI process for multi-turn use."""

    def __init__(self, binary: str, env: Mapping[str, str] | None = None) -> None:
        self.binary = binary
        self.env = dict(env or {})
        self.proc: subprocess.Popen[str] | None = None
        self.first_turn: bool = True

    def ensure_started(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            [self.binary, "--json-events", "--json-result"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, **self.env},
            bufsize=1,
        )

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()
        self.proc = None
        self.first_turn = True

    def _read_lines(self) -> Iterable[str]:
        assert self.proc is not None
        if self.proc.stdout is None:
            return []
        while True:
            line = self.proc.stdout.readline()
            if line == "":
                break
            yield line.strip()

    def send(
        self,
        prompt: str,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str] | None = None,
    ) -> tuple[Any, list[Any]]:
        self.ensure_started()
        assert self.proc is not None
        # Drain any leftover stdout before sending a new prompt
        self._drain_stdout()
        if self.proc.stdin is None:
            raise RuntimeError("ladon session stdin unavailable")
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
            events.append(parsed)
            if parsed.get("type") == "approval.request":
                if on_approval is not None:
                    decision = on_approval(parsed)
                    if decision:
                        if self.proc.stdin is None:
                            raise RuntimeError("ladon session stdin unavailable")
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
            raise RuntimeError("ladon output missing turn.result payload")
        return final, events

    def _drain_stdout(self) -> None:
        """Best-effort drain of any pending stdout to avoid bleed between turns."""
        assert self.proc is not None
        stdout = self.proc.stdout
        if stdout is None:
            return
        try:
            fd = stdout.fileno()
        except Exception:
            # Some file-like objects (e.g. StringIO in tests) don't expose fileno
            try:
                if hasattr(stdout, "seekable") and stdout.seekable():
                    stdout_flush_limit = 50
                    for _ in range(stdout_flush_limit):
                        line = stdout.readline()
                        if not line:
                            break
            except Exception:
                pass
            return
        stdout_flush_limit = 50
        for _ in range(stdout_flush_limit):
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            line = stdout.readline()
            if not line:
                break


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
    log_path: str | os.PathLike[str] | None = None,
    cancel_path: str | os.PathLike[str] | None = None,
    context_changes: bool = False,
    context_cells: int | None = None,
    context_chars: int | None = None,
    ipython=None,
) -> Callable[[str, str | None], Any]:
    """High-level helper to expose ``%%name`` in the current IPython shell."""

    return register_magic(
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
        ipython=ipython,
    )


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
    log_path: str | os.PathLike[str] | None = None,
    cancel_path: str | os.PathLike[str] | None = None,
    context_changes: bool = False,
    context_cells: int | None = None,
    context_chars: int | None = None,
    ipython=None,
) -> Callable[[str, str | None], Any]:
    """Register the ``%%name`` cell magic for ladon."""

    ip = _ensure_ipython(ipython)
    normalized = name.lower()
    mode = display_mode.lower()
    if mode not in {"auto", "markdown", "text", "none"}:
        raise ValueError(
            "display_mode must be one of 'auto', 'markdown', 'text', or 'none'"
        )

    runtime = _ensure_ladon_runtime(binary=binary, extra_env=env)
    _configure_logging(log_path)
    _configure_conversation_log()
    _configure_cancel(cancel_path)
    if context_changes:
        _configure_context()

    emit_events = show_events or debug

    def _codex_magic(line: str, cell: str | None = None) -> Any:
        prompt = _normalize_payload(line, cell)
        if not prompt:
            print("No prompt provided.")
            return None

        progress = _Progress(render=stream, cancel=lambda: _cancel_session(runtime))

        # Command prefixes for mode control
        if prompt.startswith("/"):
            cmd, _, rest = prompt.partition(" ")
            cmd = cmd.lower()

            # One-shot prompt (fresh process)
            if cmd in {"/fresh", "/once"}:
                payload = rest.strip()
                if not payload:
                    print("Usage: /fresh <prompt>")
                    return None
                result, events = ladon_run(payload)
                _log_events(events)
                if mode != "none":
                    _display_result(result, mode, progress)
                if emit_events:
                    _print_events(events)
                return result if emit_events else None

            if cmd == "/stop":
                _stop_session()
                print("ladon session stopped.")
                return None

            if cmd == "/status":
                alive = _session_alive()
                print(f"ladon session: {'running' if alive else 'stopped'}")
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
                    print("Context tracking not enabled. Use ladon.use(..., context_changes=True)")
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

        # Build prompt with proper order: template -> context -> user prompt
        session = _shared_session(runtime)
        parts = []
        original_prompt = prompt  # Save for conversation log

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
            print(f"ladon failed: {exc}")
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
        history_magic, magic_kind="cell", magic_name="ladon_history"
    )
    print(f"Registered %{normalized} cell magic.")
    return _codex_magic


def register_codex_magic(**kwargs: Any) -> Callable[[str, str | None], Any]:
    """Convenience wrapper to register ``%%codex``."""

    return register_magic(name="codex", **kwargs)


def load_ipython_extension(ipython) -> None:
    """Hook for ``%load_ext ladon.magic``."""

    use(ipython=ipython)
    # Register custom history cell magic under a unique name to avoid clash with built-in %history
    ipython.register_magic_function(history_magic, magic_kind="cell", magic_name="ladon_history")


def _ensure_ipython(ipython) -> Any:
    ip = ipython or get_ipython()
    if ip is None:
        raise RuntimeError("No active IPython session; run inside Jupyter or IPython.")
    return ip


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

    def __init__(self, render: bool, cancel: Callable[[], None] | None = None) -> None:
        self.handle = display(HTML(""), display_id=True) if render else None
        self.last_message = "Working..."
        self.last_result_text: str = ""
        self._stop = threading.Event()
        self._thread = (
            threading.Thread(target=self._loop, daemon=True) if render else None
        )
        if self._thread is not None:
            self._thread.start()

    def update(self, event: Any) -> None:
        if self.handle is None:
            return
        msg = _summarize_event(event) or self.last_message
        self.last_message = msg
        # spinner loop handles visual updates; keep latest message
        # but still do an immediate update for responsiveness
        self.handle.update(HTML(self._render_content(f"{next(self.spinner)} {msg}", spinner=True)))

    def update_message(self, message: str, *, markdown: bool = False) -> None:
        self.last_message = message
        if self.handle is None:
            return
        if markdown:
            self.handle.update(Markdown(message))
        else:
            self.handle.update(HTML(self._render_content(message, spinner=False)))

    def finish(self, message: str, markdown: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        if self.handle is None:
            return
        if markdown:
            self.handle.update(Markdown(message))
        else:
            self.handle.update(HTML(self._render_content(message, spinner=False)))
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


def _ensure_ladon_runtime(
    *,
    binary: str | None,
    extra_env: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Resolve the ladon CLI path and mutate process env accordingly."""

    runtime_env: dict[str, str] = {}
    if extra_env:
        for key, value in extra_env.items():
            os.environ[str(key)] = str(value)
            runtime_env[str(key)] = str(value)

    resolved = _resolve_ladon_binary(binary)
    if resolved is None:
        raise RuntimeError(
            "Could not find the 'ladon' CLI.\n"
            "Make sure it is on PATH, set $LADON_BIN, or call ladon.use(..., binary='/path/to/ladon')."
        )
    os.environ["LADON_BIN"] = resolved
    runtime_env["LADON_BIN"] = resolved
    return {"binary": resolved, "env": runtime_env}


def _resolve_ladon_binary(explicit: str | None) -> str | None:
    """Return a usable ladon binary path if available."""

    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)

    env_value = os.environ.get("LADON_BIN")
    if env_value:
        candidates.append(env_value)

    which_value = shutil.which("ladon")
    if which_value:
        candidates.append(which_value)

    # Heuristic: search upwards for a workspace target/{release,debug}/ladon
    for parent in Path(__file__).resolve().parents:
        target_dir = parent / "target"
        if not target_dir.exists():
            continue
        for profile in ("release", "debug"):
            candidate = target_dir / profile / "ladon"
            if candidate.is_file():
                candidates.append(str(candidate))

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
    if _SESSION is None:
        _SESSION = SharedSession(
            binary=str(runtime["binary"]),
            env=runtime.get("env") or {},
        )
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
            f.write(json.dumps({"type": "template", "data": template}, ensure_ascii=False))
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
        if hasattr(ip, 'user_ns') and '__vsc_ipynb_file__' in ip.user_ns:
            nb_path = ip.user_ns['__vsc_ipynb_file__']
            return Path(nb_path).stem
        # Try Jupyter classic/lab
        import ipykernel
        from jupyter_client import find_connection_file
        connection_file = find_connection_file()
        kernel_id = connection_file.split('-', 1)[1].split('.')[0]
        # This is a fallback - not perfect but works in many cases
        for nb_file in Path.cwd().glob('*.ipynb'):
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
            f.write(f"{'='*80}\n")
            f.write(f"USER:\n{prompt}\n\n")
            f.write(f"ASSISTANT:\n{response}\n")
            f.write(f"{'='*80}\n\n")
    except Exception:
        pass


def _log_context_block(block: str) -> None:
    if _LOG_PATH is None or not block:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "context.block", "data": block}, ensure_ascii=False))
            f.write("\n")
    except Exception:
        pass


def _log_context_debug(payload: dict[str, Any]) -> None:
    if _LOG_PATH is None:
        return
    try:
        with Path(_LOG_PATH).expanduser().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "context.debug", **payload}, ensure_ascii=False))
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
    resp_progress = _Progress(render=True if mode != "none" else False, cancel=lambda: _cancel_session(runtime))
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
            btn.on_click(lambda _b, d=decision, l=label: handler(d, l))
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
            )
        )

        # Wait until a button is clicked
        while choice["value"] is None:
            time.sleep(0.05)
        return str(choice["value"])
    except Exception:
        pass

    # Fallback to stdin
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


def _stop_session() -> None:
    global _SESSION
    if _SESSION is not None:
        _SESSION.stop()
    _SESSION = None


def _session_alive() -> bool:
    if _SESSION is None:
        return False
    proc = _SESSION.proc
    return proc is not None and proc.poll() is None


class ContextTracker:
    def __init__(self) -> None:
        self.last_seen: int = 0

    def build_block(self, max_cells: int | None, max_chars: int | None, peek: bool = False) -> str:
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
            # Skip %%codex, %%ladon_history, and line magics (both in magic form and IPython internal form)
            if (text.startswith("%%codex") or text.startswith("%%ladon_history") or
                text.startswith("%") or  # Skip all line magics like %history
                "run_cell_magic('codex'" in text or 'run_cell_magic("codex"' in text or
                "run_cell_magic('ladon_history'" in text or 'run_cell_magic("ladon_history"' in text or
                "run_line_magic(" in text):  # Skip line magic internal calls
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
        # Start tracking from current history position (ignore cells before ladon.use())
        ip = get_ipython()
        if ip is not None:
            history = ip.user_ns.get("In", [])
            if isinstance(history, list):
                _CONTEXT_TRACKER.last_seen = len(history) - 1


def _build_context_block(max_cells: int | None, max_chars: int | None, peek: bool = False) -> str:
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
