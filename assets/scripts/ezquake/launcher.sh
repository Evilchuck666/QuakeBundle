#!/usr/bin/env bash
set -euo pipefail

EZROOT="$(dirname "$(readlink -f "$0")")"

# NVIDIA / OpenGL: lower latency and avoid rare queues
export __GL_SYNC_TO_VBLANK=0
export __GL_MaxFramesAllowed=1

# SDL/X11: avoid "warp mode" and request compositor bypass
export SDL_MOUSE_RELATIVE_MODE_WARP=0
export SDL_MOUSE_RELATIVE_SCALING=0
export SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR=1

# Argument parsing
RUN_WIZARD=0
GAME_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            cat <<EOF
Usage: $(basename "$0") [--help] [--wizard] [ezQuake arguments...]

Options:
  --help    Show this help message and exit.
  --wizard  Run the setup wizard before launching ezQuake.

All other arguments are passed to ezQuake.
EOF
            exit 0
            ;;
        --wizard)
            RUN_WIZARD=1
            shift # Remove argument
            ;;
        --)
            shift
            GAME_ARGS+=("$@")
            break
            ;;
        *)
            GAME_ARGS+=("$1")
            shift # Move to next argument
            ;;
    esac
done

# Python owns configuration validation and first-run state.
SETUP_STATUS=0
python3 "$EZROOT/wizard.py" --check || SETUP_STATUS=$?
case "$SETUP_STATUS" in
    0|2) ;;
    *) exit "$SETUP_STATUS" ;;
esac

if [[ "$SETUP_STATUS" -eq 2 ]] || [[ "$RUN_WIZARD" -eq 1 ]]; then
    WIZARD_ARGS=()
    if [[ "$RUN_WIZARD" -eq 0 ]]; then
        WIZARD_ARGS+=(--auto)
    fi
    echo "Running Initial Setup Wizard..."
    if [[ -t 0 && -t 1 ]]; then
        python3 "$EZROOT/wizard.py" "${WIZARD_ARGS[@]}"
    elif command -v xterm >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
        xterm -T "ezQuake Setup Wizard" -e python3 "$EZROOT/wizard.py" "${WIZARD_ARGS[@]}"
    else
        python3 "$EZROOT/wizard.py" "${WIZARD_ARGS[@]}"
    fi
fi

# Keep generated engine settings and saves inside this installation.
cd "$EZROOT"

exec "$EZROOT/ezQuake-x86_64.AppImage" \
  -basedir "$EZROOT" \
  -nohome \
  +exec configs/autoexec.cfg \
  "${GAME_ARGS[@]}"
