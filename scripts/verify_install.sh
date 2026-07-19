#!/usr/bin/env bash
# Verifies the literal first two acceptance-walkthrough steps:
#   1. `uv tool install .` produces a working `novelizer` binary independent of cwd.
#   2. `novelizer --help` and `novelizer voices` survive a totally empty config dir
#      without an unhandled traceback.
#
# Installs into an isolated UV_TOOL_DIR/UV_TOOL_BIN_DIR — never touches the
# caller's real `uv tool` installs — and uninstalls at the end regardless of
# outcome. Safe to run from a dev checkout; does not modify the dev venv.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WORK_DIR="$(mktemp -d)"
TOOL_DIR="$WORK_DIR/uv-tools"
TOOL_BIN_DIR="$WORK_DIR/uv-tools-bin"
XDG_CONFIG_HOME_ISOLATED="$WORK_DIR/xdg-config"
RUN_DIR="$WORK_DIR/run"
mkdir -p "$TOOL_DIR" "$TOOL_BIN_DIR" "$XDG_CONFIG_HOME_ISOLATED" "$RUN_DIR"

export UV_TOOL_DIR="$TOOL_DIR"
export UV_TOOL_BIN_DIR="$TOOL_BIN_DIR"

status=0
installed=0

cleanup() {
    if [ "$installed" -eq 1 ]; then
        echo "==> Cleaning up: uv tool uninstall novelizer"
        uv tool uninstall novelizer >/dev/null 2>&1 || true
    fi
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "==> uv tool install $REPO_ROOT (isolated UV_TOOL_DIR=$TOOL_DIR)"
if ! uv tool install "$REPO_ROOT"; then
    echo "FAIL: uv tool install failed" >&2
    exit 1
fi
installed=1

BINARY="$TOOL_BIN_DIR/novelizer"
if [ ! -x "$BINARY" ]; then
    echo "FAIL: expected binary not found at $BINARY" >&2
    exit 1
fi
echo "==> Found binary: $BINARY"

echo "==> Running: novelizer --help (cwd=$RUN_DIR, empty XDG_CONFIG_HOME, no repo cwd)"
help_output="$(cd "$RUN_DIR" && XDG_CONFIG_HOME="$XDG_CONFIG_HOME_ISOLATED" "$BINARY" --help 2>&1)"
help_exit=$?
echo "$help_output"
if [ "$help_exit" -ne 0 ]; then
    echo "FAIL: novelizer --help exited $help_exit" >&2
    status=1
fi
for name in seed chapters read retcons voices autonomy; do
    if ! grep -q "$name" <<<"$help_output"; then
        echo "FAIL: --help output missing expected command '$name'" >&2
        status=1
    fi
done

echo "==> Running: novelizer voices (empty config, fresh DB path)"
DB_PATH="$RUN_DIR/smoke.db"
voices_output="$(cd "$RUN_DIR" && XDG_CONFIG_HOME="$XDG_CONFIG_HOME_ISOLATED" NOVELIZER_DB_PATH="$DB_PATH" "$BINARY" voices 2>&1)"
voices_exit=$?
echo "$voices_output"
if grep -qi "Traceback (most recent call last)" <<<"$voices_output"; then
    echo "FAIL: novelizer voices produced an unhandled traceback" >&2
    status=1
elif [ "$voices_exit" -ne 0 ]; then
    echo "OK (handled failure, exit $voices_exit, no traceback) — acceptable per plan"
else
    echo "OK: novelizer voices exited 0 against empty config"
fi

if [ "$status" -eq 0 ]; then
    echo "==> PASS: install path verified"
else
    echo "==> FAIL: see above" >&2
fi
exit "$status"
