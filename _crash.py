"""Last-resort crash handler.

Captures any unhandled Python exception (including import-time crashes,
KeyboardInterrupt, etc.) and writes a full traceback to a crash file in
the per-user data directory. Used by both the frozen exe (via the
PyInstaller runtime hook) and the dev-mode entry point.

The handler:
  * Writes a NEW crash file each invocation (timestamp suffix) so a
    repeated crash doesn't blow away earlier evidence.
  * Truncates the crash log directory to the most recent N files
    (keeps disk usage bounded).
  * Falls back to %TEMP% if the data dir cannot be created (e.g.
    permission errors). In the absolute worst case, prints to stderr.
  * Uses ONLY the Python stdlib so it cannot fail due to a missing
    third-party dependency.

Activate from any entry point:
    import _crash
    _crash.install()
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import traceback
from pathlib import Path

_INSTALLED = False
_KEEP_LAST_N_CRASHES = 20


def _resolve_log_dir() -> Path:
    """Determine where to write crash dumps.

    Order of preference:
      1. $EMAIL_ASSISTANT_DATA_DIR/logs (explicit override)
      2. %LOCALAPPDATA%\\EmailAssistant\\logs (Windows, frozen mode default)
      3. ~/.email-assistant/logs (POSIX fallback)
      4. %TEMP%\\EmailAssistant_logs (last resort if home is unwritable)
    """
    candidates: list[Path] = []
    explicit = os.environ.get("EMAIL_ASSISTANT_DATA_DIR")
    if explicit:
        candidates.append(Path(explicit) / "logs")
    if os.name == "nt":
        appdata = os.environ.get("LOCALAPPDATA")
        if appdata:
            candidates.append(Path(appdata) / "EmailAssistant" / "logs")
    candidates.append(Path.home() / ".email-assistant" / "logs")
    tmp = os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp"
    candidates.append(Path(tmp) / "EmailAssistant_logs")

    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except Exception:
            continue
    # Truly desperate: return CWD; caller will catch the write error.
    return Path(".")


def _trim_old_crashes(log_dir: Path) -> None:
    try:
        crashes = sorted(log_dir.glob("crash_*.txt"), key=lambda p: p.stat().st_mtime)
        for old in crashes[:-_KEEP_LAST_N_CRASHES]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _write_crash(exc_type, exc_value, exc_tb) -> Path | None:
    log_dir = _resolve_log_dir()
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"crash_{ts}_{os.getpid()}.txt"
    path = log_dir / fname
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Email Assistant crash report\n")
            f.write(f"Time:       {_dt.datetime.now().isoformat()}\n")
            f.write(f"Pid:        {os.getpid()}\n")
            f.write(f"Argv:       {sys.argv!r}\n")
            f.write(f"Frozen:     {getattr(sys, 'frozen', False)}\n")
            f.write(f"Executable: {sys.executable}\n")
            f.write(f"Python:     {sys.version}\n")
            f.write(f"Cwd:        {os.getcwd()}\n")
            f.write(f"Platform:   {sys.platform}\n")
            f.write("\n--- Traceback ---\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
            f.write("\n--- Environment (selected) ---\n")
            for k in (
                "LOCALAPPDATA", "APPDATA", "USERPROFILE", "TEMP", "PATH",
                "EMAIL_ASSISTANT_DATA_DIR", "PYTHONPATH",
            ):
                v = os.environ.get(k)
                if v is not None:
                    f.write(f"{k}={v}\n")
        _trim_old_crashes(log_dir)
        return path
    except Exception:
        return None


def install() -> None:
    """Install sys.excepthook to dump uncaught exceptions to a crash file."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    prev_hook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        # Don't record clean Ctrl+C interrupts; they're user-initiated and
        # not actionable bug reports.
        if issubclass(exc_type, KeyboardInterrupt):
            return prev_hook(exc_type, exc_value, exc_tb)

        path = _write_crash(exc_type, exc_value, exc_tb)
        # Always also emit to stderr so console users see something
        # immediately. The crash file is for users who launched via a
        # shortcut and the console window vanished on exit.
        try:
            prev_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass
        if path is not None:
            try:
                sys.stderr.write(
                    f"\n[Email Assistant] Crash report written to:\n  {path}\n"
                )
            except Exception:
                pass

    sys.excepthook = _hook


def manual_record(exc: BaseException) -> Path | None:
    """Record a caught exception manually (e.g. from a top-level try/except)."""
    return _write_crash(type(exc), exc, exc.__traceback__)
