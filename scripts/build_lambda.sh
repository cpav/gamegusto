#!/usr/bin/env bash
# Build the deployment package for the v2 API Lambda.
#
# Produces dist/api.zip containing the app plus its dependencies, built for
# the Lambda runtime rather than for this machine.
#
# Two decisions worth knowing:
#
#   * Wheels are fetched for manylinux/x86_64, not for the host. pydantic and
#     friends ship compiled extensions, and a macOS wheel fails at import
#     inside Lambda with an error that does not mention architecture.
#
#   * boto3 is bundled rather than relying on the runtime's copy. The runtime
#     provides one, but the version floats, and the agent depends on Bedrock
#     ConverseStream — the streaming path this whole design rests on. Pinning
#     the tested version costs bundle size and buys not debugging a streaming
#     failure that only appears in production.
#
# Gmail deps are deliberately absent: bootstrap.py imports that source lazily,
# so the API runs without them and the bundle stays far smaller.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/dist/lambda-build"
OUT="$ROOT/dist/api.zip"

PYTHON_VERSION="3.13"

rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD"

# uv when available (this repo's venvs are uv-managed and have no pip),
# otherwise pip. The two spell the target platform differently.
echo "==> installing dependencies for linux/x86_64, py$PYTHON_VERSION"
if command -v uv >/dev/null 2>&1; then
  uv pip install --quiet \
    --target "$BUILD" \
    --python-platform x86_64-manylinux2014 \
    --python-version "$PYTHON_VERSION" \
    --only-binary :all: \
    -r "$ROOT/requirements.txt" \
    -r "$ROOT/requirements-api.txt"
else
  "${PIP:-pip}" install --quiet \
    --target "$BUILD" \
    --platform manylinux2014_x86_64 \
    --python-version "$PYTHON_VERSION" \
    --implementation cp \
    --only-binary=:all: --upgrade \
    -r "$ROOT/requirements.txt" \
    -r "$ROOT/requirements-api.txt"
fi

echo "==> copying application code"
for item in api agent services models config.py bootstrap.py; do
  cp -R "$ROOT/$item" "$BUILD/$item"
done

# The entrypoint. The Lambda Web Adapter runs this as the handler: it starts a
# normal uvicorn server, and LWA translates between Lambda invocations and
# ordinary HTTP. Mangum was the obvious alternative and is the wrong choice —
# it returns a single buffered response, which would silently convert our
# token-by-token SSE stream into one message delivered at the end.
cat > "$BUILD/run.sh" <<'ENTRY'
#!/bin/bash
set -euo pipefail

# Resolve the Brave key from SSM into the environment the app already
# expects. Doing it here rather than in config.py keeps secret resolution at
# the deployment boundary: the application reads BRAVE_API_KEY from the
# environment exactly as it does on a laptop, and the value never sits in
# Terraform state or in the function's visible configuration.
if [[ -n "${BRAVE_PARAMETER:-}" ]]; then
  BRAVE_API_KEY="$(python -c "
import boto3, os
ssm = boto3.client('ssm')
print(ssm.get_parameter(Name=os.environ['BRAVE_PARAMETER'], WithDecryption=True)['Parameter']['Value'])
")"
  export BRAVE_API_KEY
fi

# IGDB cover art, stored as "client_id:client_secret" in one parameter. A miss
# is not fatal — the enricher falls back to the image search.
if [[ -n "${IGDB_PARAMETER:-}" ]]; then
  IGDB_PAIR="$(python -c "
import boto3, os
ssm = boto3.client('ssm')
try:
    print(ssm.get_parameter(Name=os.environ['IGDB_PARAMETER'], WithDecryption=True)['Parameter']['Value'])
except Exception:
    print('')
")"
  # Reject the unset parameter AND the example pasted verbatim. Angle brackets
  # never appear in real Twitch credentials, and treating "<client-id>" as one
  # means every lookup fails its token request and silently falls back — the
  # feature looks configured and is not.
  if [[ "$IGDB_PAIR" == *:* && "$IGDB_PAIR" != placeholder* && "$IGDB_PAIR" != *"<"* ]]; then
    export IGDB_CLIENT_ID="${IGDB_PAIR%%:*}"
    export IGDB_CLIENT_SECRET="${IGDB_PAIR#*:}"
  else
    echo "IGDB credentials not configured; covers fall back to image search." >&2
  fi
fi

exec python -m uvicorn --factory api.main:build --host 0.0.0.0 --port "${AWS_LWA_PORT:-8000}"
ENTRY
chmod +x "$BUILD/run.sh"

# Only bytecode and vendored test suites. Note what is NOT pruned: *.dist-info.
# Stripping it saves a few MB and breaks any library that reads its own
# version through importlib.metadata — a failure that appears only in Lambda.
echo "==> pruning bytecode and vendored tests"
find "$BUILD" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> zipping"
mkdir -p "$(dirname "$OUT")"
(cd "$BUILD" && zip -qr "$OUT" .)

printf "built %s (%s, unpacked %s)\n" \
  "${OUT#"$ROOT"/}" \
  "$(du -h "$OUT" | cut -f1 | tr -d ' ')" \
  "$(du -sh "$BUILD" | cut -f1 | tr -d ' ')"
