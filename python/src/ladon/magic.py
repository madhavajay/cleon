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


class SharedSession:
    """Lightweight persistent ladon CLI process for multi-turn use."""

    def __init__(self, binary: str, env: Mapping[str, str] | None = None) -> None:
        self.binary = binary
        self.env = dict(env or {})
        self.proc: subprocess.Popen[str] | None = None

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
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]:
        self.ensure_started()
        assert self.proc is not None
        if self.proc.stdin is None:
            raise RuntimeError("ladon session stdin unavailable")
        self.proc.stdin.write(prompt + "\n")
        self.proc.stdin.flush()

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

        if final is None:
            raise RuntimeError("ladon output missing turn.result payload")
        return final, events


def use(
    name: str = "codex",
    *,
    binary: str | None = None,
    env: Mapping[str, str] | None = None,
    display_mode: DisplayMode = "auto",
    show_events: bool = False,
    debug: bool = False,
    stream: bool = True,
    prompt_user: bool = True,
    log_path: str | os.PathLike[str] | None = None,
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
    prompt_user: bool = True,
    log_path: str | os.PathLike[str] | None = None,
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

    emit_events = show_events or debug

    def _codex_magic(line: str, cell: str | None = None) -> Any:
        prompt = _normalize_payload(line, cell)
        if not prompt:
            print("No prompt provided.")
            return None

        progress = _Progress(render=stream)

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

            print(f"Unknown command: {cmd}")
            print("Commands: /fresh, /stop, /status, /new")
            return None

        try:
            result, events = _shared_session(runtime).send(
                prompt,
                on_event=_chain(progress.update, _log_event),
                on_approval=_prompt_approval,
            )
        except Exception as exc:  # pragma: no cover - surfaced to notebook
            print(f"ladon failed: {exc}")
            raise

        if mode != "none":
            _display_result(result, mode, progress)
        if emit_events:
            _print_events(events)
        if prompt_user:
            _maybe_prompt_followup(runtime, mode, progress)
        return result if emit_events else None

    ip.register_magic_function(_codex_magic, magic_kind="cell", magic_name=normalized)
    print(f"Registered %{normalized} cell magic.")
    return _codex_magic


def register_codex_magic(**kwargs: Any) -> Callable[[str, str | None], Any]:
    """Convenience wrapper to register ``%%codex``."""

    return register_magic(name="codex", **kwargs)


def load_ipython_extension(ipython) -> None:
    """Hook for ``%load_ext ladon.magic``."""

    use(ipython=ipython)


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

    def __init__(self, render: bool) -> None:
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
        self.handle.update(HTML(f"<code>{next(self.spinner)} {msg}</code>"))

    def update_message(self, message: str, *, markdown: bool = False) -> None:
        self.last_message = message
        if self.handle is None:
            return
        if markdown:
            self.handle.update(Markdown(message))
        else:
            self.handle.update(HTML(message))

    def finish(self, message: str, markdown: bool = False) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        if self.handle is None:
            return
        if markdown:
            self.handle.update(Markdown(message))
        else:
            self.handle.update(HTML(message))
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
    resp_progress = _Progress(render=True if mode != "none" else False)
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

        buttons = []
        choice: dict[str, str | None] = {"value": None}
        out = widgets.Output()

        def handler(decision: str, label: str):
            choice["value"] = decision
            with out:
                clear_output()
                print(f"Selected: {label}")

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
