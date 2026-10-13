#!/usr/bin/env bash
set -euo pipefail

VKROOT="$(dirname "$(readlink -f "$0")")"

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
Usage: $(basename "$0") [--help] [--wizard] [vkQuake arguments...]

Options:
  --help    Show this help message and exit.
  --wizard  Run the setup wizard before launching vkQuake.

All other arguments are passed to vkQuake.
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
python3 "$VKROOT/setup.py" --check || SETUP_STATUS=$?
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
        python3 "$VKROOT/setup.py" "${WIZARD_ARGS[@]}"
    elif command -v xterm >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
        xterm -T "vkQuake Setup Wizard" -e python3 "$VKROOT/setup.py" "${WIZARD_ARGS[@]}"
    else
        python3 "$VKROOT/setup.py" "${WIZARD_ARGS[@]}"
    fi
fi

# Keep generated engine settings and saves inside this installation.
cd "$VKROOT"

exec "$VKROOT/vkQuake.AppImage" \
  -basedir "$VKROOT" \
  -userdir "$VKROOT" \
  +exec configs/autoexec.cfg \
  "${GAME_ARGS[@]}"
