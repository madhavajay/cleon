"""CLI helpers for launching Jupyter with Cleon preinstalled."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
import venv


DEFAULT_ENV = Path(
    os.environ.get("CLEON_JUPYTER_ENV", "~/.cache/cleon/jupyter-env")
).expanduser()


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _env_python(env_dir: Path) -> Path:
    if platform.system().lower().startswith("win"):
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _ensure_env(env_dir: Path, *, use_uv: bool = True, upgrade: bool = True) -> Path:
    env_dir = env_dir.expanduser()
    env_dir.mkdir(parents=True, exist_ok=True)

    py_path = _env_python(env_dir)
    if not py_path.exists():
        if use_uv and shutil.which("uv"):
            _run(["uv", "venv", str(env_dir)])
        else:
            venv.EnvBuilder(with_pip=True).create(env_dir)

    packages = ["cleon", "cleon-jupyter-extension"]
    if not _has_jupyter(py_path):
        packages.append("jupyterlab")

    # Use uv pip if we created the venv with uv
    if use_uv and shutil.which("uv"):
        installer = ["uv", "pip", "install", "--python", str(py_path)]
        if upgrade:
            installer.append("-U")
        installer += packages
    else:
        installer = [str(py_path), "-m", "pip", "install"]
        if upgrade:
            installer.append("-U")
        installer += packages
    _run(installer)
    return py_path


def _launch_jupyter(py_path: Path, tool: str, args: list[str]) -> int:
    cmd = [str(py_path), "-m", "jupyter", tool, *args]
    return subprocess.call(cmd)


def _has_jupyter(py_path: Path) -> bool:
    try:
        probe = [
            str(py_path),
            "-c",
            "import importlib.util; import sys; sys.exit(0 if importlib.util.find_spec('jupyterlab') else 1)",
        ]
        return (
            subprocess.call(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            == 0
        )
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cleon", description="Cleon CLI helpers")
    sub = parser.add_subparsers(dest="cmd")

    jp = sub.add_parser(
        "jupyter", help="Launch Jupyter with Cleon + extension installed"
    )
    jp.add_argument("tool", choices=["lab", "notebook"], nargs="?", default="lab")
    jp.add_argument(
        "tool_args", nargs=argparse.REMAINDER, help="Arguments passed to Jupyter"
    )
    jp.add_argument(
        "--env",
        dest="env",
        default=None,
        help="Path to managed virtualenv (default: ~/.cache/cleon/jupyter-env)",
    )
    jp.add_argument(
        "--no-uv", action="store_true", help="Skip using uv even if available"
    )
    jp.add_argument(
        "--no-upgrade", action="store_true", help="Skip -U when installing packages"
    )

    args = parser.parse_args(argv)

    if args.cmd in {None, "jupyter"}:
        env_dir = Path(args.env) if getattr(args, "env", None) else DEFAULT_ENV
        py_path = _ensure_env(
            env_dir,
            use_uv=not getattr(args, "no_uv", False),
            upgrade=not getattr(args, "no_upgrade", False),
        )
        return _launch_jupyter(
            py_path, getattr(args, "tool", "lab"), getattr(args, "tool_args", [])
        )

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
