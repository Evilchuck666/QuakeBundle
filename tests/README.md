# Installer tests

The installer entry point is `singleplayer.py`; its supporting modules live in
the `quake_installer/` package alongside it.

Run the same offline checks as GitHub Actions from the repository root:

```sh
python -m pip install tqdm
python -B -m unittest discover -s tests -v
```

The `Test singleplayer` workflow runs on pushes, pull requests and manual
dispatches. Its matrix covers the platforms with a vkQuake build selected by
the installer:

| Platform | GitHub runner | Python |
| --- | --- | --- |
| Linux x64 | `ubuntu-24.04` | 3.9–3.14 |
| Windows x64 | `windows-2025` | 3.9, 3.14 |
| Windows ARM64 | `windows-11-arm` | 3.14 |
| macOS Intel | `macos-15-intel` | 3.9, 3.14 |
| macOS Apple Silicon | `macos-15` | 3.14 |

The runner labels follow the [GitHub runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Python uses the host architecture by default with [setup-python](https://github.com/actions/setup-python#supported-architectures).
Python 3.9 is the script's declared minimum; setup-python does not provide it
natively for macOS or Windows ARM64.

The tests exercise the CLI in a separate process, the installation flow with
synthetic game data, preservation of saves/configuration, retries, cleanup,
platform paths, archive extraction, engine selection, download handling, and
SteamCMD output capture through the runner's native PTY or Windows pipes.
The ezQuake and vkQuake wizard checks cover first-run state and configuration
recovery. Both wizards are integrated into their standalone `launcher.py`.
`test_launcher_platforms.py` exercises both scripts with simulated Linux,
Windows and macOS platforms: executable selection, bundle metadata, explicit
engine paths, argument forwarding, exit statuses, missing engines, environment
overrides, console selection and Windows `pythonw.exe` without standard streams.
It also runs `--setup-only` natively in a subprocess on each CI host. On POSIX
hosts, the macOS terminal's shell wrapper runs with a real Python child to verify
quoting, completion status and temporary-file cleanup. Terminal automation itself
is simulated, as are Windows console creation and platform engine execution.
The existing Linux integration tests execute fake AppImages and a fake xterm to
verify setup, mods, argument forwarding, failure handling and graphics settings.
Files and installation directories are temporary; no Steam credentials or
GitHub secrets are needed. Steam, release metadata, Rosetta probes and macOS
DMG commands are simulated. File downloads use local file URLs.

`test_shortcuts.py` covers shortcut destinations, template downloads, quoting,
replacement and failure recovery. On Linux with GIO installed, a harmless Python
recorder is launched through a real desktop entry parser. Native Windows checks
create and inspect `.lnk` files in a temporary desktop directory. Native macOS
checks load the generated workflow with Automator and apply the PNG with AppKit
inside a temporary Applications directory. These checks do not launch the game.

These checks do not download the commercial game, authenticate against Steam,
check live release availability, or launch vkQuake. A complete installation
and gameplay check still requires a Steam account that owns Quake and the
platform's native runtime dependencies.

## Using the standalone launchers

After downloading the launcher and icon, `singleplayer.py` creates a per-user
shortcut named **Quake Single Player (vkQuake Port)**:

| Platform | Shortcut | Target and icon |
| --- | --- | --- |
| GNU/Linux | `~/.local/share/applications/vkQuake.desktop` | `launcher.py`, `quake.svg` |
| Windows | User's Desktop, including redirected/OneDrive desktops | `launcher.exe`, `quake.ico` |
| macOS | `~/Applications/Quake Single Player (vkQuake Port).app` | Automator runs `launcher.py`, with `quake.png` as its custom icon |

`multiplayer.py` creates **Quake Multiplayer (ezQuake Port)** using
`ezQuake.desktop` on Linux, a separate `.lnk` on Windows and a separate `.app`
on macOS. Its icons are `ezquake.svg`, `ezquake.ico` and `ezquake.png`,
respectively. Both engines' shortcuts can coexist; reinstalling either one
preserves the other's shortcut.

Linux downloads the repository's desktop template and sets absolute paths,
keeping a terminal available for setup. Linux and macOS shortcuts retain the
Python executable used to run the installer; that Python installation must
remain available. Windows uses PowerShell and Windows Script Host to create the
shortcut. macOS uses the system Automator application stub, a Run Shell Script
workflow and AppKit to apply the PNG icon. Reinstalling updates the shortcut;
creation failures are reported and leave the installed game data available.

Each launcher needs Python 3.9 or later and stays beside its game data. It locates
the following engines in that directory (names are matched without case):

| Platform | ezQuake | vkQuake |
| --- | --- | --- |
| Linux | `ezQuake-x86_64.AppImage` | `vkQuake.AppImage` |
| Windows | `ezquake.exe` | `vkQuake.exe` |
| macOS | `ezQuake.app` or `ezQuake-*.app` | `vkQuake.app` or `vkQuake-*.app` |

macOS bundles must contain `Contents/Info.plist` with a valid
`CFBundleExecutable` pointing to a file under `Contents/MacOS`. Linux and macOS
executables need execute permission. Install an engine build compatible with the
host architecture; the launcher does not download engines or add emulation.

Use `python launcher.py --engine "path/to/engine"` to select a different binary,
an externally installed engine, or a macOS `.app`. Relative engine paths are
resolved from the calling directory; game data and shared configuration still
come from the launcher's directory. If several engines match, select one with
`--engine`. Windows overrides must point to `.exe` files. The existing `--mod`,
`--wizard`, `--setup-only`, `--check` and `--auto` modes remain available; arguments
after `--` go directly to the engine, including any literal `--engine` option.

Interactive setup uses the current terminal when available. Otherwise it opens
xterm on Linux with `DISPLAY` set, a new console on Windows (using `python.exe`
when started through `pythonw.exe`), or Terminal through `osascript` on macOS.
It waits for setup to finish and stops on cancellation or failure. macOS may
request permission to control Terminal; if automation is unavailable, run
`python3 launcher.py --setup-only` in Terminal first. Linux without xterm/display
can still use redirected input. `--setup-only` always uses the current input and
does not open another terminal or require an installed engine.

The launchers retain their shared configuration and mod behavior. vkQuake uses
`-userdir` on Linux/macOS and the installation working directory on Windows;
ezQuake retains `-nohome`. SDL mouse settings apply on all three systems, while
the X11 compositor and ezQuake NVIDIA/OpenGL overrides are applied only on Linux.
Start the game through `launcher.py` to use this setup flow.

Before claiming native gameplay validation on Windows or macOS, manually check
first run, repeat run, `--wizard` cancellation, a mod, paths containing spaces,
and configuration/save locations with the actual engine. On macOS also check
Terminal automation and closing the setup tab; on Windows check `pythonw.exe`.
