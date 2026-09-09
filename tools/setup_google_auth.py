"""One-time Google sign-in for Gmail storage tools (no LLM / full .env required).

Usage:
    .\.venv\Scripts\python.exe tools\setup_google_auth.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

import tls_setup  # noqa: E402, F401

from dotenv import load_dotenv  # noqa: E402
from providers.google_auth import GoogleAuth  # noqa: E402


def main() -> int:
    load_dotenv()
    secrets = Path(os.getenv("GOOGLE_CLIENT_SECRETS_PATH", "client_secret.json"))
    token = Path(os.getenv("GOOGLE_TOKEN_CACHE_PATH", "google_token.json"))

    print("Gmail sign-in for storage tools")
    print(f"  Client secrets: {secrets.resolve()}")
    print(f"  Token cache:    {token.resolve()}")
    print()

    if not secrets.exists():
        print("ERROR: client_secret.json not found.")
        print()
        print("Fix:")
        print("  1. Google Cloud Console -> APIs & Services -> Credentials")
        print("  2. Create OAuth client ID -> Desktop app -> Download JSON")
        print(f"  3. Save as: {secrets.resolve()}")
        print("  4. Enable Gmail API: https://console.cloud.google.com/apis/library/gmail.googleapis.com")
        return 2

    try:
        auth = GoogleAuth(client_secrets_path=secrets, token_cache_path=token)
        creds = auth.get_credentials()
        print("SUCCESS: Google sign-in complete.")
        print(f"  Token valid: {creds.valid}")
        print(f"  Cached at:   {token.resolve()}")
        print()
        print("Next: .\\VerifyArchiveAuth.cmd --google-only")
        return 0
    except Exception as e:
        print(f"ERROR: {e}")
        print()
        print("Common fixes:")
        print("  - Add your Gmail address under OAuth consent screen -> Test users")
        print("  - Enable Gmail API in Google Cloud project")
        print("  - Delete google_token.json and run this script again")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
