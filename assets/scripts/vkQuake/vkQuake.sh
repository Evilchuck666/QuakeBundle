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

SETUP_MARKER="$VKROOT/id1/configs/.wizard-complete"

for config in autoexec.cfg game.cfg player.cfg; do
    if [[ ! -f "$VKROOT/id1/configs/$config" ]]; then
        echo "Missing configuration: $VKROOT/id1/configs/$config" >&2
        echo "Copy assets/cfg/vkQuake/*.cfg into the installation's id1/configs directory." >&2
        exit 1
    fi
done

if [[ ! -f "$SETUP_MARKER" ]] || [[ "$RUN_WIZARD" -eq 1 ]]; then
    echo "Running Initial Setup Wizard..."
    if [[ -t 0 && -t 1 ]]; then
        python3 "$VKROOT/wizard.py"
    elif command -v xterm >/dev/null 2>&1 && [[ -n "${DISPLAY:-}" ]]; then
        xterm -T "vkQuake Setup Wizard" -e python3 "$VKROOT/wizard.py"
    else
        python3 "$VKROOT/wizard.py"
    fi
fi

# Keep generated engine settings and saves inside this installation.
cd "$VKROOT"

exec "$VKROOT/vkQuake.AppImage" \
  -basedir "$VKROOT" \
  -userdir "$VKROOT" \
  +exec configs/autoexec.cfg \
  "${GAME_ARGS[@]}"
