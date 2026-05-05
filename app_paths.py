"""Cross-mode (dev / frozen) path resolution.

The agent runs in two modes:
  - Dev:    `python main.py --once` from the project source tree.
  - Frozen: `EmailAssistant.exe --once` after Inno Setup install.

These two modes have different layouts on disk, so any code that
loads a bundled resource (.env.example, samples/) or writes to a runtime
file (.env, state.db, token_cache.bin, logs/agent.log) needs to ask
this module which directory to use.

Conventions:

  data_dir()   -> Where runtime state lives (the user's writable area).
                  Frozen on Windows: %LOCALAPPDATA%\\EmailAssistant
                  Dev:              the current working directory
                  Override:         $EMAIL_ASSISTANT_DATA_DIR

  bundle_dir() -> Where read-only resources shipped with the app live.
                  Frozen one-folder: dir containing EmailAssistant.exe
                  Frozen one-file:   sys._MEIPASS (PyInstaller temp dir)
                  Dev:               this file's parent (project root)

  install_dir()-> Where the program files were installed (frozen only;
                  same as bundle_dir() in one-folder mode).

  find_resource("name", "alt-name") -> Search data_dir() first (so a user
                  edit wins) then bundle_dir(). Returns None if missing.

Stdlib only: this file is imported by both runtime_hook.py and main.py
before third-party deps are guaranteed available.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    """Where runtime state lives (writable, per-user)."""
    explicit = os.environ.get("EMAIL_ASSISTANT_DATA_DIR")
    if explicit:
        return Path(explicit)
    if is_frozen():
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(
                Path.home() / "AppData" / "Local"
            )
            return Path(base) / "EmailAssistant"
        return Path.home() / ".email-assistant"
    # Dev: project root = wherever the user invoked python from. Almost
    # always the repo root because that's where main.py is.
    return Path.cwd()


def bundle_dir() -> Path:
    """Where read-only bundled resources live."""
    if is_frozen():
        # PyInstaller --onefile sets _MEIPASS to a temp extraction dir.
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # PyInstaller --onefolder: resources sit alongside the exe.
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def install_dir() -> Path:
    """Where program files were installed. Same as bundle_dir() in one-folder."""
    if is_frozen():
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def find_resource(*names: str) -> Path | None:
    """Locate a resource by trying multiple names across data + bundle dirs.

    A user edit in data_dir() always wins over the bundled copy.
    """
    bases = [data_dir(), bundle_dir()]
    for base in bases:
        for name in names:
            p = base / name
            if p.exists():
                return p
    return None


def ensure_data_dir() -> Path:
    """Create the data dir if missing and return it. Best-effort."""
    d = data_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def diagnostics() -> dict:
    """Snapshot all interesting paths for --diagnose / crash reports."""
    return {
        "is_frozen": is_frozen(),
        "data_dir": str(data_dir()),
        "bundle_dir": str(bundle_dir()),
        "install_dir": str(install_dir()),
        "cwd": str(Path.cwd()),
        "executable": sys.executable,
        "python": sys.version,
        "platform": sys.platform,
        "argv": list(sys.argv),
        "env_overrides": {
            k: os.environ.get(k, "")
            for k in (
                "EMAIL_ASSISTANT_DATA_DIR",
                "LOCALAPPDATA",
                "APPDATA",
                "USERPROFILE",
                "TEMP",
            )
            if os.environ.get(k)
        },
        "_meipass": getattr(sys, "_MEIPASS", None),
    }
