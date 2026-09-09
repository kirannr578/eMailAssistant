"""Verify Google and Microsoft credentials for Gmail archive tools."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_tools = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_tools))

import tls_setup  # noqa: E402, F401

from dotenv import load_dotenv  # noqa: E402


def _check_google() -> tuple[bool, str]:
    load_dotenv()
    secrets = Path(os.getenv("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json"))
    token = Path(os.getenv("GOOGLE_TOKEN_CACHE_PATH", "google_token.json"))
    if not secrets.exists():
        return False, f"Missing Google client secrets: {secrets}"
    if not token.exists():
        return False, f"Missing Google token cache: {token} (run: python main.py --auth)"
    try:
        from gmail_storage_common import load_google_client

        client = load_google_client()
        ids = client.search_message_ids("in:inbox", max_results=1)
        return True, f"Google OK (sample query returned {len(ids)} message id(s))"
    except Exception as e:
        return False, (
            f"Google auth failed: {e}\n"
            "       Fix: run .\\.venv\\Scripts\\python.exe tools\\setup_google_auth.py"
        )


def _check_local_onedrive() -> tuple[bool, str]:
    from providers.local_onedrive import detect_onedrive_root

    root = detect_onedrive_root()
    if root:
        return True, f"Local OneDrive sync folder OK: {root}"
    return False, (
        "Local OneDrive folder not found. Sign in to OneDrive desktop, or set "
        "GMAIL_ARCHIVE_LOCAL_ROOT in .env to your sync path."
    )


def _check_graph_onedrive() -> tuple[bool, str]:
    load_dotenv()
    client_id = os.getenv("MS_CLIENT_ID", "").strip()
    token = Path(os.getenv("MS_TOKEN_CACHE_PATH", "token_cache.bin"))
    if not client_id:
        return False, "MS_CLIENT_ID not set (optional if using local OneDrive folder)"
    if not token.exists():
        return False, f"Missing Microsoft token cache: {token}"
    try:
        from providers.ms_graph_auth import GraphAuth
        from providers.onedrive import OneDriveClient

        auth = GraphAuth(
            client_id=client_id,
            tenant_id=os.getenv("MS_TENANT_ID", "common"),
            token_cache_path=token,
        )
        od = OneDriveClient(auth)
        root_id = od.ensure_folder("")
        return True, f"Graph OneDrive API OK (drive root id: {root_id[:8]}...)"
    except Exception as e:
        return False, f"Graph OneDrive API failed: {e}"


def _check_onedrive() -> tuple[bool, str]:
    local_ok, local_msg = _check_local_onedrive()
    if local_ok:
        return True, local_msg
    graph_ok, graph_msg = _check_graph_onedrive()
    if graph_ok:
        return True, graph_msg
    return False, f"{local_msg} | {graph_msg}"


def main() -> int:
    p = argparse.ArgumentParser(description="Verify auth for Gmail storage tools.")
    p.add_argument("--google-only", action="store_true")
    p.add_argument("--microsoft-only", action="store_true")
    args = p.parse_args()

    checks: list[tuple[str, tuple[bool, str]]] = []
    if not args.microsoft_only:
        checks.append(("Google (Gmail)", _check_google()))
    if not args.google_only:
        checks.append(("OneDrive destination", _check_onedrive()))

    ok = True
    for name, (passed, detail) in checks:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}: {detail}")
        if not passed:
            ok = False
    if ok and not args.google_only and not args.microsoft_only:
        print()
        print("Tip: With OneDrive desktop installed, archive uses your local sync folder")
        print("     automatically — MS_CLIENT_ID is only needed for --graph-api mode.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
