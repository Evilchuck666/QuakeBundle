#!/usr/bin/env bash
set -euo pipefail

VKROOT="$(dirname "$(readlink -f "$0")")"

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
        *)
            GAME_ARGS+=("$1")
            shift # Move to next argument
            ;;
    esac
done

PLAYER_CFG="$VKROOT/vkQuake.cfg"

if [[ ! -f "$PLAYER_CFG" ]] || [[ "$RUN_WIZARD" -eq 1 ]]; then
    if [[ -t 1 ]]; then
        echo "Running Initial Setup Wizard..."
        python3 "$VKROOT/wizard.py"
    else
        if command -v xterm >/dev/null 2>&1; then
            xterm -T "vkQuake Setup Wizard" \
              -e bash -lc "cd \"$VKROOT\" && \
                           echo 'Running Initial Setup Wizard...' && \
                           python3 \"$VKROOT/wizard.py\"; \
                           echo; \
                           echo 'Press Enter to close this window...'; \
                           read -r _"
        else
            python3 "$VKROOT/wizard.py"
        fi
    fi
fi


exec "$VKROOT/vkQuake.AppImage" \
  -basedir "$VKROOT" \
  +exec configs/autoexec.cfg \
  "${GAME_ARGS[@]}"
