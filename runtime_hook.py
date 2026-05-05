"""PyInstaller runtime hook: install crash handler before any user code.

PyInstaller injects this script into the frozen exe before main.py loads.
That means it catches even import-time crashes (e.g. a missing hidden
import, a corrupted bundle, a TLS failure during truststore.inject) -
which is exactly the kind of crash the user can't see otherwise because
the launcher / Start Menu shortcut closes its console on exit.

Wired in EmailAssistant.spec via:
    runtime_hooks=["runtime_hook.py"]
"""
from __future__ import annotations

# Use a try/except so a bug in our own crash handler can't take down the
# whole exe before main.py even gets a chance to print its argparse help.
try:
    import _crash  # type: ignore
    _crash.install()
except Exception:
    pass
