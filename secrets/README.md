# secrets/ (git-ignored)

Drop local credential files here. Nothing in this directory is committed
except this README.

Expected files for the Gmail integration:

- `gmail_client.json` — the OAuth client JSON downloaded from Google Cloud
  Console (Desktop app). Point `GMAIL_CREDENTIALS_PATH` at it.
- `gmail_token.json` — written automatically after the first interactive
  sign-in. Point `GMAIL_TOKEN_PATH` at it. Do not create by hand.

Never commit real credential files. They are excluded via `.gitignore`.
