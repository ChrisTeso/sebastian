#!/bin/zsh
set -euo pipefail

SERVICE_HOME="$HOME/Library/Application Support/Sebastian"
APP_PATH="$HOME/Applications/Sebastian.app"
PLIST_PATH="$HOME/Library/LaunchAgents/com.christeso.sebastian.plist"
LOG_DIR="$HOME/Library/Logs/Sebastian"
CLI_PATH="$HOME/.local/bin/sebastian"
LABEL="com.christeso.sebastian"
KEYCHAIN_SERVICE="com.christeso.sebastian.openai"

if /bin/launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  /bin/launchctl bootout "gui/$(id -u)/$LABEL" || true
fi

for target in "$SERVICE_HOME" "$APP_PATH" "$LOG_DIR"; do
  case "$target" in
    "$HOME/Library/Application Support/Sebastian"|"$HOME/Applications/Sebastian.app"|"$HOME/Library/Logs/Sebastian")
      [[ -e "$target" ]] && rm -rf -- "$target"
      ;;
    *)
      echo "Refusing unexpected uninstall target: $target" >&2
      exit 1
      ;;
  esac
done
rm -f -- "$PLIST_PATH" "$CLI_PATH"
/usr/bin/security delete-generic-password -a "$USER" -s "$KEYCHAIN_SERVICE" >/dev/null 2>&1 || true

echo "Sebastian's app, runtime, state, logs, LaunchAgent, CLI, and Keychain item were removed."
echo "Messages and conversation history were not modified. The removal is not recoverable unless backed up."
