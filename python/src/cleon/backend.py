"""Backend abstractions for cleon agents."""

from __future__ import annotations

import json
import os
import queue
import select
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

import importlib.resources as importlib_resources

from ._cleon import run as cleon_run  # type: ignore[import-not-found]
from .settings import (
    get_agent_settings,
)


class AgentBackend(Protocol):
    """Interface implemented by concrete agent backends."""

    name: str
    supports_async: bool

    def first_turn(self) -> bool: ...

    def send(
        self,
        prompt: str,
        *,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]: ...

    def run_once(self, prompt: str) -> tuple[Any, list[Any]]: ...

    def stop(self) -> "SessionStopInfo": ...

    def session_alive(self) -> bool: ...


@dataclass
class SessionStopInfo:
    """Metadata captured when a backend session stops."""

    session_id: str | None
    resume_command: str | None


_SESSION_LOCK = threading.Lock()


def _log_backend_event(agent: str, event: str, details: Mapping[str, Any]) -> None:
    """Lightweight logger for backend timing/debug events."""

    path = os.environ.get("CLEON_LOG_PATH", "./cleon.log")
    try:
        payload = {
            "ts": time.time(),
            "agent": agent,
            "event": event,
            **dict(details),
        }
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False))
            fh.write("\n")
    except Exception:
        # Logging must never break runtime behavior
        pass


class SharedSession:
    """Lightweight persistent CLI process for multi-turn Codex usage."""

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
            self._drain_stdout(capture_metadata=True)
            if self.proc.stdin is None:
                raise RuntimeError("cleon session stdin unavailable")
            single_line_prompt = prompt.replace("\n", " ⏎ ")
            self.proc.stdin.write(single_line_prompt + "\n")
            self.proc.stdin.flush()
            self.first_turn = False

            events: list[Any] = []
            final: Any | None = None
            for raw in self._read_lines():
                try:
                    parsed = json.loads(raw)
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

            self._drain_stdout()
            time.sleep(0.1)
            self._drain_stdout()

            if final is None:
                raise RuntimeError("cleon output missing turn.result payload")
            return final, events

    def mark_first_turn(self) -> None:
        """Force the next send() to treat the prompt as the first turn."""
        self.first_turn = True

    def _drain_stdout(self, capture_metadata: bool = False) -> None:
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


class CodexBackend:
    """Codex CLI backend implementation."""

    name = "codex"
    supports_async = True

    def __init__(
        self,
        *,
        binary: str | None,
        extra_env: Mapping[str, str] | None,
        session_id: str | None,
    ) -> None:
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

        self._binary = resolved
        self._env = runtime_env
        self._session_id = session_id
        self._session: SharedSession | None = SharedSession(
            binary=self._binary, env=self._env, session_id=self._session_id
        )

    def first_turn(self) -> bool:
        session = self._ensure_session()
        return session.first_turn

    def reset_first_turn(self) -> None:
        session = self._ensure_session()
        session.mark_first_turn()

    def _ensure_session(self) -> SharedSession:
        if self._session is None or self._session.stopped:
            self._session = SharedSession(
                binary=self._binary, env=self._env, session_id=self._session_id
            )
        return self._session

    def send(
        self,
        prompt: str,
        *,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]:
        session = self._ensure_session()
        return session.send(prompt, on_event=on_event, on_approval=on_approval)

    def run_once(self, prompt: str) -> tuple[Any, list[Any]]:
        return cleon_run(prompt)

    def stop(self) -> SessionStopInfo:
        if self._session is None:
            return SessionStopInfo(None, None)
        self._session.stop()
        info = SessionStopInfo(
            session_id=self._session.session_id,
            resume_command=self._session.resume_command,
        )
        self._session_id = None
        self._session = None
        return info

    def session_alive(self) -> bool:
        if self._session is None:
            return False
        proc = self._session.proc
        return proc is not None and proc.poll() is None


def _resolve_cleon_binary(explicit: str | None) -> str | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)

    env_value = os.environ.get("CLEON_BIN")
    if env_value:
        candidates.append(env_value)

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


def _resolve_pi_command(config: Any) -> list[str]:
    if isinstance(config, str):
        return [config]
    if isinstance(config, Iterable):
        cmd = [str(part) for part in config]
        if cmd:
            return cmd
    pi_path = shutil.which("pi")
    if pi_path:
        return [pi_path]
    return ["npx", "pi"]


def _resolve_gemini_command(config: Any, explicit: str | None) -> list[str]:
    """Resolve how to launch the gemini CLI."""

    if explicit:
        return [explicit]
    if isinstance(config, str):
        return [config]
    if isinstance(config, Iterable):
        cmd = [str(part) for part in config]
        if cmd:
            return cmd

    packaged = _packaged_gemini_bundle()
    if packaged:
        node_path = shutil.which("node")
        if not node_path:
            raise RuntimeError(
                "Node.js (node) is required to run the packaged gemini.js. Install Node 20+ or set GEMINI command explicitly."
            )
        return [node_path, packaged]

    gemini_path = shutil.which("gemini")
    if gemini_path:
        return [gemini_path]
    return ["npx", "@google/gemini-cli@latest"]


def _packaged_gemini_bundle() -> str | None:
    try:
        candidate = importlib_resources.files(__package__).joinpath("bin/gemini.js")
        if candidate.is_file():
            return str(candidate)
    except Exception:
        return None
    return None


class PiProcess:
    def __init__(
        self, settings: Mapping[str, Any], extra_env: Mapping[str, str] | None
    ) -> None:
        self._settings = settings
        self._env = os.environ.copy()
        if extra_env:
            for key, value in extra_env.items():
                self._env[str(key)] = str(value)
        env_override = settings.get("env")
        if isinstance(env_override, Mapping):
            for key, value in env_override.items():
                self._env[str(key)] = str(value)
        self._cmd = _resolve_pi_command(settings.get("command"))
        self._cwd = Path(settings.get("cwd") or os.getcwd())
        self._timeout = float(settings.get("response_timeout") or 240.0)
        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._proc: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._last_stderr: list[str] = []
        self._start()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _build_command(self) -> list[str]:
        cmd = list(self._cmd)
        arg_setting = self._settings.get("args")
        extra_args: list[str] = []
        if isinstance(arg_setting, str):
            extra_args = [arg_setting]
        elif isinstance(arg_setting, Iterable):
            extra_args = [str(part) for part in arg_setting]
        base_args = ["--mode", "rpc"]
        if self._settings.get("no_session", True):
            base_args.append("--no-session")
        provider = self._settings.get("provider")
        if provider:
            base_args.extend(["--provider", str(provider)])
        model = self._settings.get("model")
        if model:
            base_args.extend(["--model", str(model)])
        system_prompt = self._settings.get("system_prompt")
        if system_prompt:
            base_args.extend(["--system-prompt", str(system_prompt)])
        combined = cmd + base_args + extra_args
        return [str(part) for part in combined]

    def _start(self) -> None:
        command = self._build_command()
        try:
            self._proc = subprocess.Popen(
                command,
                cwd=str(self._cwd),
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Failed to launch pi CLI. Install it via `npm install -g @mariozechner/pi-coding-agent` "
                "or set agents.claude.command in cleon.settings()."
            ) from exc
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                if not line:
                    continue
                try:
                    parsed = json.loads(line.strip())
                except Exception:
                    continue
                self._queue.put(parsed)
        finally:
            self._queue.put({"type": "pi.process_exit"})

    def _read_stderr(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        try:
            self._last_stderr = []
            for line in proc.stderr:
                if line:
                    self._last_stderr.append(line.rstrip())
                    if len(self._last_stderr) > 50:
                        self._last_stderr = self._last_stderr[-50:]
        finally:
            try:
                proc.stderr.close()
            except Exception:
                pass

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def restart(self) -> None:
        """Restart the pi CLI process to pick up fresh auth/config without kernel restarts."""
        self.stop()
        self._queue = queue.Queue()
        self._start()

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if not proc or proc.stdin is None:
            raise RuntimeError("pi backend is not running.")
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError(
                "Failed to send request to pi backend: Broken pipe. "
                'Please run cleon.auth("claude") to authenticate.'
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to send request to pi backend: {exc}") from exc

    def send_prompt(
        self,
        prompt: str,
        on_event: Callable[[Any], None] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        self._send({"type": "prompt", "message": prompt})
        events: list[dict[str, Any]] = []
        final_message: dict[str, Any] | None = None
        while True:
            try:
                raw = self._queue.get(timeout=self._timeout)
            except queue.Empty:
                raise RuntimeError("Timed out waiting for pi response.")
            if not isinstance(raw, Mapping):
                # Some pi CLI invocations (e.g., when the CLI is missing) can emit plain
                # numbers before exiting; ignore any non-mapping payloads so we can
                # surface a meaningful error instead of crashing.
                continue
            if raw.get("type") == "pi.process_exit":
                stderr_output = (
                    "\n".join(self._last_stderr[-10:]) if self._last_stderr else ""
                )
                hint = ""
                if (
                    "no api key" in stderr_output.lower()
                    and "anthropic" in stderr_output.lower()
                ):
                    hint = ' pi has no Claude API key. Please run cleon.auth("claude").'
                elif (
                    "auth" in stderr_output.lower() or "login" in stderr_output.lower()
                ):
                    hint = " Try running cleon.auth() to authenticate."
                elif stderr_output:
                    hint = f"\nStderr: {stderr_output}"
                raise RuntimeError(f"pi backend exited unexpectedly.{hint}")
            if raw.get("type") == "error":
                raise RuntimeError(raw.get("error") or "pi backend reported an error.")
            translated = _translate_pi_event(raw)
            events.append(translated)
            if on_event:
                try:
                    on_event(translated)
                except Exception:
                    pass
            if raw.get("type") == "turn_end":
                final_message = raw.get("message")
                break
        final_text = _extract_pi_text(final_message) if final_message else ""
        if not final_text:
            final_text = "Claude response completed."
        result = {"final_message": final_text, "agent": "claude"}
        return result, events


class PiBackend:
    name = "claude"
    supports_async = True

    def __init__(
        self,
        *,
        binary: str | None = None,
        extra_env: Mapping[str, str] | None = None,
        session_id: str | None = None,
    ) -> None:
        settings = get_agent_settings("claude")
        self._process = PiProcess(settings, extra_env)
        self._send_lock = threading.Lock()
        self._first_turn = True

    def first_turn(self) -> bool:
        return self._first_turn

    def send(
        self,
        prompt: str,
        *,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]:
        del on_approval
        with self._send_lock:
            retry_once = True
            while True:
                try:
                    return self._process.send_prompt(prompt, on_event=on_event)
                except RuntimeError as exc:
                    msg = str(exc).lower()
                    transient = (
                        "pi backend exited unexpectedly" in msg
                        or "pi backend is not running" in msg
                    )
                    if transient and retry_once:
                        retry_once = False
                        try:
                            self._process.restart()
                            continue
                        except Exception:
                            pass
                    raise
                finally:
                    self._first_turn = False

    def run_once(self, prompt: str) -> tuple[Any, list[Any]]:
        return self.send(prompt)

    def stop(self) -> SessionStopInfo:
        self._process.stop()
        return SessionStopInfo(None, None)

    def restart(self) -> None:
        """Restart the underlying pi process and reset first-turn state."""
        with self._send_lock:
            self._process.restart()
            self._first_turn = True

    def reset_first_turn(self) -> None:
        with self._send_lock:
            self._first_turn = True

    def session_alive(self) -> bool:
        return self._process.alive


class GeminiProcess:
    """Threaded Gemini CLI process with async support."""

    def __init__(
        self,
        settings: Mapping[str, Any],
        extra_env: Mapping[str, str] | None,
        explicit_binary: str | None,
    ) -> None:
        self._settings = settings
        self._env = os.environ.copy()
        if extra_env:
            for key, value in extra_env.items():
                self._env[str(key)] = str(value)
        env_override = settings.get("env")
        if isinstance(env_override, Mapping):
            for key, value in env_override.items():
                self._env[str(key)] = str(value)

        self._cmd = _resolve_gemini_command(settings.get("command"), explicit_binary)
        self._timeout = float(settings.get("response_timeout") or 240.0)
        self._log_prefix = "[gemini]"
        self._proc: subprocess.Popen[str] | None = None
        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._start()

    def _build_command(self) -> list[str]:
        cmd = list(self._cmd)
        base_args = ["--output-format", "stream-json", "--stdin-rpc"]

        model = self._settings.get("model")
        if model:
            base_args.extend(["--model", str(model)])

        approval = self._settings.get("approval_mode")
        if approval:
            if str(approval).lower() == "yolo":
                base_args.append("--yolo")
            else:
                base_args.extend(["--approval-mode", str(approval)])

        allowed_tools = self._settings.get("allowed_tools")
        if isinstance(allowed_tools, Iterable):
            tools = [str(t) for t in allowed_tools if str(t)]
            if tools:
                base_args.extend(["--allowed-tools", ",".join(tools)])

        extra_args: list[str] = []
        arg_setting = self._settings.get("args")
        if isinstance(arg_setting, str):
            extra_args = [arg_setting]
        elif isinstance(arg_setting, Iterable):
            extra_args = [str(part) for part in arg_setting]

        return [str(part) for part in (cmd + base_args + extra_args)]

    def _start(self) -> None:
        command = self._build_command()
        start_time = time.time()
        _log_backend_event(
            "gemini",
            "process.start",
            {
                "cmd": command,
                "cwd": os.getcwd(),
                "env_override": bool(self._settings.get("env")),
            },
        )
        try:
            self._proc = subprocess.Popen(
                command,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            _log_backend_event(
                "gemini",
                "process.started",
                {
                    "duration_ms": int((time.time() - start_time) * 1000),
                    "cmd": command,
                },
            )
        except FileNotFoundError as exc:
            _log_backend_event(
                "gemini",
                "process.error",
                {"error": str(exc), "cmd": command},
            )
            raise RuntimeError(
                "Gemini CLI is not installed. Install with `npm install -g @google/gemini-cli` "
                "or `brew install gemini-cli`, or rely on npx by leaving command unset."
            ) from exc
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                if not line:
                    continue
                try:
                    parsed = json.loads(line.strip())
                except Exception:
                    continue
                self._queue.put(parsed)
        finally:
            self._queue.put({"type": "gemini.process_exit"})

    def _read_stderr(self) -> None:
        proc = self._proc
        if not proc or not proc.stderr:
            return
        try:
            for _ in proc.stderr:
                continue
        finally:
            try:
                proc.stderr.close()
            except Exception:
                pass

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def restart(self) -> None:
        """Restart the Gemini CLI process."""
        self.stop()
        self._queue = queue.Queue()
        self._start()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if not proc or proc.stdin is None:
            raise RuntimeError("Gemini backend is not running.")
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to send request to Gemini backend: {exc}"
            ) from exc

    def send_prompt(
        self,
        prompt: str,
        on_event: Callable[[Any], None] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        start_ts = time.time()
        _log_backend_event(
            "gemini",
            "send.start",
            {"prompt_chars": len(prompt), "queue_size": self._queue.qsize()},
        )
        self._send({"type": "prompt", "message": prompt})
        events: list[dict[str, Any]] = []
        final_parts: list[str] = []
        first_event_ts: float | None = None
        last_raw: Mapping[str, Any] | None = None

        while True:
            try:
                raw = self._queue.get(timeout=self._timeout)
            except queue.Empty:
                _log_backend_event(
                    "gemini",
                    "send.timeout",
                    {"prompt_chars": len(prompt), "timeout_s": self._timeout},
                )
                raise RuntimeError("Timed out waiting for Gemini response.")

            if not isinstance(raw, Mapping):
                continue

            if raw.get("type") == "gemini.process_exit":
                _log_backend_event(
                    "gemini",
                    "process.exit",
                    {"prompt_chars": len(prompt)},
                )
                raise RuntimeError("Gemini backend exited unexpectedly.")

            translated = _translate_gemini_event(raw)
            events.append(translated)
            if first_event_ts is None:
                first_event_ts = time.time()
            last_raw = raw

            if on_event:
                try:
                    on_event(translated)
                except Exception:
                    pass

            if raw.get("type") == "message" and raw.get("role") == "assistant":
                text = _extract_gemini_text(raw)
                if text:
                    final_parts.append(text)

            if raw.get("type") == "result":
                break

        final_text = "\n".join(part for part in final_parts if part)
        if not final_text:
            final_text = "Gemini response completed."
        _log_backend_event(
            "gemini",
            "send.complete",
            {
                "prompt_chars": len(prompt),
                "events": len(events),
                "first_event_ms": int((first_event_ts - start_ts) * 1000)
                if first_event_ts
                else None,
                "total_ms": int((time.time() - start_ts) * 1000),
                "stats": last_raw.get("stats")
                if isinstance(last_raw, Mapping)
                else None,
            },
        )
        result = {"final_message": final_text, "agent": "gemini"}
        return result, events

    def _log(self, msg: str) -> None:
        try:
            sys.stderr.write(f"{self._log_prefix} {msg}\n")
            sys.stderr.flush()
        except Exception:
            pass


class GeminiBackend:
    name = "gemini"
    supports_async = True

    def __init__(
        self,
        *,
        binary: str | None = None,
        extra_env: Mapping[str, str] | None = None,
        session_id: str | None = None,
    ) -> None:
        del session_id
        settings = get_agent_settings("gemini")
        self._process = GeminiProcess(settings, extra_env, binary)
        self._send_lock = threading.Lock()
        self._first_turn = True

    def first_turn(self) -> bool:
        return self._first_turn

    def reset_first_turn(self) -> None:
        with self._send_lock:
            self._first_turn = True

    def send(
        self,
        prompt: str,
        *,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]:
        del on_approval
        with self._send_lock:
            retry_once = True
            while True:
                try:
                    result, events = self._process.send_prompt(
                        prompt, on_event=on_event
                    )
                    self._first_turn = False
                    return result, events
                except RuntimeError as exc:
                    msg = str(exc).lower()
                    transient = (
                        "gemini backend exited unexpectedly" in msg
                        or "gemini backend is not running" in msg
                    )
                    if transient and retry_once:
                        retry_once = False
                        try:
                            _log_backend_event(
                                "gemini",
                                "process.restart",
                                {"reason": msg},
                            )
                            self._process.restart()
                            continue
                        except Exception:
                            pass
                    raise

    def run_once(self, prompt: str) -> tuple[Any, list[Any]]:
        return self.send(prompt)

    def stop(self) -> SessionStopInfo:
        self._process.stop()
        return SessionStopInfo(None, None)

    def session_alive(self) -> bool:
        return self._process.alive


def resolve_backend(
    *,
    agent: str,
    binary: str | None,
    extra_env: Mapping[str, str] | None,
    session_id: str | None,
) -> AgentBackend:
    agent_name = agent.lower()
    if agent_name in {"codex", "default"}:
        return CodexBackend(binary=binary, extra_env=extra_env, session_id=session_id)
    if agent_name in {"claude", "anthropic"}:
        return PiBackend(binary=binary, extra_env=extra_env, session_id=session_id)
    if agent_name in {"gemini"}:
        return GeminiBackend(binary=binary, extra_env=extra_env, session_id=session_id)
    raise ValueError(f"Unknown cleon backend '{agent}'.")


def _translate_pi_event(event: dict[str, Any]) -> dict[str, Any]:
    etype = event.get("type")
    if not isinstance(etype, str):
        return {"type": "claude.event", "event": event}
    payload: dict[str, Any] = {"type": f"claude.{etype}"}
    if etype in {"message_start", "message_update", "message_end", "turn_end"}:
        message = event.get("message")
        payload["text"] = _extract_pi_text(message)
        payload["raw"] = message
    if etype == "tool_execution_start":
        payload.update(
            {
                "tool": event.get("toolName"),
                "args": event.get("args"),
                "tool_call_id": event.get("toolCallId"),
            }
        )
    if etype == "tool_execution_end":
        payload.update(
            {
                "tool": event.get("toolName"),
                "result": event.get("result"),
                "error": event.get("isError"),
                "tool_call_id": event.get("toolCallId"),
            }
        )
    if etype in {"agent_start", "agent_end", "turn_start"}:
        payload.update({k: v for k, v in event.items() if k != "type"})
    return payload


def _extract_pi_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(
        part.strip() for part in parts if isinstance(part, str) and part.strip()
    )


def _translate_gemini_event(event: dict[str, Any]) -> dict[str, Any]:
    etype = event.get("type")
    if not isinstance(etype, str):
        return {"type": "gemini.event", "event": event}
    payload: dict[str, Any] = {"type": f"gemini.{etype}"}
    if etype == "message":
        payload["text"] = _extract_gemini_text(event)
        payload["raw"] = event
        if isinstance(event.get("role"), str):
            payload["role"] = event.get("role")
    if etype in {"tool_use", "tool_result"}:
        payload.update({k: v for k, v in event.items() if k != "type"})
    if etype == "result" and "stats" in event:
        payload["stats"] = event.get("stats")
    return payload


def _extract_gemini_text(event: dict[str, Any]) -> str:
    content = event.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                val = item.get("content")
            else:
                val = None
            if isinstance(val, str):
                parts.append(val)
        return "\n".join(part.strip() for part in parts if part)
    return ""
