"""Make Python's TLS trust the OS trust store.

Importing this module is enough - it monkey-patches ssl.SSLContext at import
time. After import, every subsequent HTTPS call (requests, httpx via openai
SDK, urllib, etc.) will verify certificates against the OS's native trust
store (Windows certificate store, macOS Keychain, Linux ca-certificates)
instead of certifi's bundled root list.

Why this matters:
- Enterprise networks routinely intercept HTTPS to api.openai.com,
  graph.microsoft.com, etc. via a TLS-inspection proxy that re-signs
  certificates with a corporate root CA.
- IT installs that corporate CA in the OS trust store so browsers and
  PowerShell work fine.
- But Python's `requests` and `httpx` use certifi by default - they don't
  see the corporate CA, so EVERY external API call fails with
  "CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate".
- truststore.inject_into_ssl() fixes this in one line, no cert exporting.

This must be imported BEFORE the first SSL connection happens. Every entry
point of the agent (main.py, setup_wizard.py, tools/test_analyze.py)
imports this at the top.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import truststore
    truststore.inject_into_ssl()
    logger.debug("truststore: injected OS trust store into ssl.SSLContext")
except ImportError:
    # Soft-fail in environments where truststore isn't installed yet (e.g.
    # someone running the wizard before `pip install` finished). The agent
    # itself requires it, so requirements.txt pulls it in.
    logger.warning(
        "truststore not installed; HTTPS calls will use certifi only. "
        "If you're behind a corporate TLS-inspection proxy, install it: "
        "pip install truststore"
    )
except Exception as e:  # noqa: BLE001
    # Any unexpected truststore failure shouldn't prevent the agent from
    # starting - it'll just fall back to certifi behavior.
    logger.warning("truststore.inject_into_ssl() failed: %s", e)
