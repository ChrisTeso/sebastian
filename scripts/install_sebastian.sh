#!/bin/zsh
set -euo pipefail

SOURCE_ROOT="${0:A:h:h}"
SERVICE_HOME="$HOME/Library/Application Support/Sebastian"
APP_PATH="$HOME/Applications/Sebastian.app"
APP_BINARY="$APP_PATH/Contents/MacOS/Sebastian"
PLIST_PATH="$HOME/Library/LaunchAgents/com.christeso.sebastian.plist"
LOG_DIR="$HOME/Library/Logs/Sebastian"
CLI_PATH="$HOME/.local/bin/sebastian"
KEYCHAIN_SERVICE="com.christeso.sebastian.openai"
LABEL="com.christeso.sebastian"
OLD_MINI_ME_PLIST="$HOME/Library/LaunchAgents/com.christeso.minime.watcher.plist"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Sebastian requires macOS." >&2
  exit 1
fi
if [[ ! -d /System/Applications/Messages.app ]]; then
  echo "Messages.app is unavailable." >&2
  exit 1
fi

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi
"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "Python 3.11 or newer is required." >&2
  exit 1
}

umask 077
mkdir -p "$SERVICE_HOME" "$HOME/Applications" "$HOME/Library/LaunchAgents" "$LOG_DIR" "$HOME/.local/bin"
touch "$SERVICE_HOME/DISABLED"
if /bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "Stopping the existing Sebastian service before replacing its runtime..."
  if ! /bin/launchctl bootout "gui/$(id -u)/$LABEL"; then
    if /bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "Unable to stop the existing Sebastian service; installation aborted." >&2
      exit 1
    fi
  fi
fi
if [[ -f "$OLD_MINI_ME_PLIST" ]]; then
  echo "Note: an older Mini Me watcher LaunchAgent exists. Sebastian will not change or load it."
fi

echo "Installing Sebastian's private Python environment..."
STAGE_DIR="$(mktemp -d "$SERVICE_HOME/.install.XXXXXX")"
STAGED_VENV="$STAGE_DIR/venv"
"$PYTHON_BIN" -m venv "$STAGED_VENV"
"$STAGED_VENV/bin/python" -m pip install --disable-pip-version-check --quiet --upgrade pip
"$STAGED_VENV/bin/python" -m pip install --disable-pip-version-check --quiet "$SOURCE_ROOT"

if [[ -e "$SERVICE_HOME/venv.previous" ]]; then
  rm -rf -- "$SERVICE_HOME/venv.previous"
fi
if [[ -e "$SERVICE_HOME/venv" ]]; then
  mv "$SERVICE_HOME/venv" "$SERVICE_HOME/venv.previous"
fi
if ! mv "$STAGED_VENV" "$SERVICE_HOME/venv"; then
  [[ -e "$SERVICE_HOME/venv.previous" ]] && mv "$SERVICE_HOME/venv.previous" "$SERVICE_HOME/venv"
  echo "Runtime replacement failed; the prior runtime was restored." >&2
  exit 1
fi
rm -rf -- "$SERVICE_HOME/venv.previous" "$STAGE_DIR"

if [[ ! -f "$SERVICE_HOME/config.json" ]]; then
  install -m 600 "$SOURCE_ROOT/config.sebastian.example.json" "$SERVICE_HOME/config.json"
fi

echo "Building Sebastian.app as the stable macOS permission identity..."
mkdir -p "$APP_PATH/Contents/MacOS"
install -m 644 "$SOURCE_ROOT/resources/Info.plist" "$APP_PATH/Contents/Info.plist"
/usr/bin/swiftc "$SOURCE_ROOT/native/SebastianLauncher.swift" \
  -framework Security -framework LocalAuthentication -o "$APP_BINARY"
/usr/bin/codesign --force --sign - --identifier com.christeso.sebastian "$APP_PATH" >/dev/null

cat > "$CLI_PATH" <<EOF
#!/bin/zsh
export SEBASTIAN_HOME="$SERVICE_HOME"
exec "$SERVICE_HOME/venv/bin/python" -m sebastian.cli "\$@"
EOF
chmod 700 "$CLI_PATH"

"$SERVICE_HOME/venv/bin/python" - "$PLIST_PATH" "$APP_BINARY" "$LOG_DIR" <<'PY'
import plistlib
import sys
from pathlib import Path

path, binary, logs = map(Path, sys.argv[1:])
payload = {
    "Label": "com.christeso.sebastian",
    "ProgramArguments": [str(binary), "run"],
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "ProcessType": "Background",
    "StandardOutPath": str(logs / "launchagent.out.log"),
    "StandardErrorPath": str(logs / "launchagent.err.log"),
}
with path.open("wb") as handle:
    plistlib.dump(payload, handle)
path.chmod(0o600)
PY
plutil -lint "$PLIST_PATH" >/dev/null

echo
echo "Create a dedicated OpenAI API project/key in the OpenAI platform before continuing."
if "$PYTHON_BIN" - "$APP_BINARY" <<'PY'
import subprocess
import sys

try:
    result = subprocess.run(
        [sys.argv[1], "doctor-keychain"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(1)
raise SystemExit(result.returncode)
PY
then
  echo "Reusing Sebastian's existing login Keychain item."
else
  /usr/bin/security delete-generic-password -a "$USER" -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1 || true
  read -s "?Paste Sebastian's dedicated OpenAI API key (input is hidden): " openai_key
  echo
  if [[ -z "$openai_key" ]]; then
    echo "An OpenAI API key is required." >&2
    exit 1
  fi
  /usr/bin/security add-generic-password -U \
    -a "$USER" \
    -s "$KEYCHAIN_SERVICE" \
    -l "Sebastian OpenAI API key" \
    -T "$APP_BINARY" \
    -w "$openai_key"
  unset openai_key
fi

echo
echo "ACTION REQUIRED: grant Full Disk Access to this exact app:"
echo "  $APP_PATH"
echo "Open System Settings > Privacy & Security > Full Disk Access, click +, press Command-Shift-G,"
echo "paste the path above, add Sebastian.app, and turn it on. Do not grant access to unrelated apps."
open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'
read "?Press Return only after Sebastian.app is enabled in Full Disk Access..."

echo
echo "The next read-only probe may ask whether Sebastian can control Messages."
echo "Choose Allow. This checks account availability but does not send a message."
"$CLI_PATH" doctor --permissions

echo
echo "Testing the OpenAI Responses API without displaying the key..."
"$CLI_PATH" doctor --openai
"$CLI_PATH" test --dry-run

rm -f "$SERVICE_HOME/DISABLED"
/bin/launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
/bin/launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo
echo "Sebastian is installed and monitoring explicitly tagged sent or received messages."
echo "Run: $CLI_PATH status"
read "?Run the permission-gated live end-to-end test now? [y/N] " live_answer
if [[ "$live_answer" == [Yy]* ]]; then
  "$CLI_PATH" test --live
else
  echo "Live E2E deferred. Run 'sebastian test --live', then send @sebastian in Messages."
fi
