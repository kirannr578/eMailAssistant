# -*- mode: python ; coding: utf-8 -*-
# =====================================================================
# PyInstaller spec for Email Assistant
# ---------------------------------------------------------------------
# Build:    pyinstaller EmailAssistant.spec --clean --noconfirm
# Output:   dist\EmailAssistant\EmailAssistant.exe (one-folder bundle)
#
# We use one-folder mode (not --onefile) because the agent is invoked
# every 5 min by Task Scheduler; one-file mode re-extracts to %TEMP% on
# every launch which is slow and leaves orphan dirs.
# =====================================================================

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

block_cipher = None

# ---------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------
# main.py performs lazy imports inside _build_email_calendar_storage so
# the provider modules aren't picked up by static analysis. List them
# explicitly. setup_wizard is also lazy-loaded under --setup.
hiddenimports = [
    # Crash handler + path resolver must always be in the bundle.
    "_crash",
    "app_paths",
    # Provider stack (lazy in main.py)
    "providers",
    "providers.base",
    "providers.outlook",
    "providers.calendar",
    "providers.ms_graph_auth",
    "providers.onedrive",
    "providers.gmail",
    "providers.google_auth",
    "providers.google_calendar",
    "providers.google_drive",
    "providers.telegram",
    "providers.whatsapp_meta",
    "providers.notifier",
    # Setup wizard (lazy)
    "setup_wizard",
    # Document downloader uses these
    "dateutil",
    "dateutil.parser",
    "dateutil.tz",
    # Pydantic v2 - PyInstaller usually finds these but list them
    # explicitly so a CI build can't silently strip the Rust core.
    "pydantic",
    "pydantic.deprecated.decorator",
    "pydantic_core",
    "pydantic_core._pydantic_core",
]

# google-api-python-client uses dynamic discovery; pull in everything.
hiddenimports += collect_submodules("googleapiclient")
hiddenimports += collect_submodules("google_auth_oauthlib")
hiddenimports += collect_submodules("google.auth")
hiddenimports += collect_submodules("msal")

# ---------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------
# Some packages ship JSON/YAML/discovery files that PyInstaller misses
# unless we collect them explicitly.
datas = []
datas += collect_data_files("googleapiclient")  # discovery_cache, etc.
datas += collect_data_files("google_auth_oauthlib")
datas += collect_data_files("certifi")           # CA bundle (truststore fallback)
datas += collect_data_files("tzdata", include_py_files=False)

# Project data files: ship .env.example and the sample bid email so the
# user can poke around the install dir if they want a reference. These
# go INTO the bundle (read-only) - the runtime data dir is separate.
datas += [
    (".env.example", "."),
    ("README.md", "."),
    ("samples", "samples"),
]

# Some libraries (truststore, openai, msal) advertise a version via
# package metadata; copy_metadata ensures importlib.metadata works in
# the frozen build.
for pkg in ("openai", "msal", "twilio", "truststore", "google-auth", "google-auth-oauthlib"):
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass


# ---------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------
a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook.py"],
    excludes=[
        # Tests + dev-only deps - exclude to keep the bundle small.
        "pytest",
        "_pytest",
        "pluggy",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ---------------------------------------------------------------------
# Console exe (the agent + wizard are interactive / log to stdout)
# ---------------------------------------------------------------------
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EmailAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # UPX corrupts some Win10 installs; not worth it
    console=True,     # MUST be true: --setup wizard reads stdin, --auth prints device codes
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="installer\\app.ico",   # uncomment once you ship an icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EmailAssistant",
)
