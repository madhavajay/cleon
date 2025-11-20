"""Backend abstractions for cleon agents."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

import importlib.resources as importlib_resources

from ._cleon import run as cleon_run  # type: ignore[import-not-found]


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
    raise ValueError(f"Unknown cleon backend '{agent}'.")
