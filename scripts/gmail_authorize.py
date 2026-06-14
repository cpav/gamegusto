"""One-time interactive Gmail authorization helper.

Runs the read-only OAuth flow for the Desktop-app client, opens a browser for
consent, and caches the resulting token. This is a developer setup utility, not
part of the application runtime; ``GmailSource`` (task 4.4) consumes the cached
token produced here.

Usage (inside the venv):
    python scripts/gmail_authorize.py
"""

from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_env_file  # noqa: E402

# Read-only scope only — the app never requests broader Gmail access (Req 4.1).
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    """Run the consent flow and write the cached token to GMAIL_TOKEN_PATH."""
    load_env_file()
    creds_path = os.environ.get("GMAIL_CREDENTIALS_PATH", "")
    token_path = os.environ.get("GMAIL_TOKEN_PATH", "")

    if not creds_path or not os.path.isfile(creds_path):
        print("ERROR: GMAIL_CREDENTIALS_PATH is unset or the file is missing.")
        sys.exit(1)
    if not token_path:
        print("ERROR: GMAIL_TOKEN_PATH is unset.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    print("Opening your browser for Google consent (read-only Gmail)...")
    print("If it does not open automatically, copy the printed URL into a browser.")
    creds = flow.run_local_server(port=0, open_browser=True)

    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w") as handle:
        handle.write(creds.to_json())
    # Restrict token file permissions to the owner.
    os.chmod(token_path, 0o600)

    print(f"SUCCESS: token cached at {token_path}")
    print("Scopes granted:", list(creds.scopes or []))


if __name__ == "__main__":
    main()
