# QuakeBundle technical guide

[Back to the project overview](README.md)

Quake installer and launcher built with **Qt6 / PySide6**. One window for the
campaign with vkQuake and multiplayer with ezQuake + nQuake, folder selection,
player settings, Steam authentication, and installation progress.

## Standalone application

The distribution package contains **Python, Qt6, TLS certificates, icons, and
configuration files in a single executable**. Users do not need to install
Python, pip, or Qt. Extract the package and open `QuakeBundle` (Linux/macOS) or
`QuakeBundle.exe` (Windows). No accompanying `_internal` directory is needed.

### Download for Windows

1. Open [Build Qt6 desktop on GitHub Actions](https://github.com/Evilchuck666/QuakeBundle/actions/workflows/desktop.yml)
   and select a successful run.
2. Under **Artifacts**, download **QuakeBundle-windows-2025** (Windows x64).
   GitHub requires you to sign in to download artifacts.
3. Extract the artifact ZIP, then extract the
   `QuakeBundle-windows-x64.zip` archive inside it.
4. Open **`QuakeBundle.exe`**. This is the only file needed to run the GUI.

You do not need to install Python, pip, or PySide6, or run any commands.
**Code → Download ZIP** downloads the source code, not the standalone application.

The GUI and its installation logic are written in Python;
PyInstaller bundles the interpreter into the application using `onefile` mode.
At startup, it extracts libraries and resources to a temporary folder, which it
removes on normal shutdown. This adds startup time and requires temporary space;
on Linux, that location must allow the extracted libraries to execute.
This is a self-extracting executable, not an AppImage or a universal static binary.
Each operating system and architecture needs its own build. See the
[PyInstaller documentation](https://pyinstaller.org/en/stable/operating-mode.html).

The application installs and launches the engines directly, without requiring an
external interpreter to play from the GUI. Initial settings and icons are copied
from the bundled resources to the installation folder. The GUI is the installer's
only interface. Once installation is complete, it creates an engine shortcut that
works without Python: in the Linux application menu, on the Windows desktop, or
in `~/Applications` on macOS. The shortcut is updated when reinstalling.
Existing configuration files are preserved, except for the player fields edited
in the window.

Installation requires an Internet connection and a Steam account that owns Quake.
SteamCMD, the engines, and the game data are downloaded during installation; they
are not included in the package. Password and Steam Guard prompts appear in the
window, and responses are sent through process input, never as arguments.
QuakeBundle does not save or log them; SteamCMD may retain its own Steam session.
On Windows, login uses a hidden virtual console (ConPTY) to receive password and
Steam Guard prompts. This support is included in the standalone package; no
additional Python installation or components are required.

The GUI keeps SteamCMD between installations and reuses it for both editions,
checking for updates before using it. SteamCMD is installed in
`~/.local/share/steamcmd` on Linux (or under `XDG_DATA_HOME` if set),
`%LOCALAPPDATA%/steamcmd` on Windows, and
`~/Library/Application Support/steamcmd` on macOS. When installation finishes,
cleanup removes temporary game downloads and keeps SteamCMD.

For a recognized installation, **Play** works without signing in again and lets
you select installed expansions. Settings are saved in the game's configuration
files. The folder and username are remembered through QSettings.

## Development and builds

### Build scripts

With `uv` installed, run the appropriate script **on the target operating system**:

| System | Command | Generated package |
| --- | --- | --- |
| GNU/Linux | `./scripts/build-linux.sh` | `dist/QuakeBundle-linux-x64.tar.gz` |
| Windows PowerShell | `.\scripts\build-windows.ps1` | `dist/QuakeBundle-windows-x64.zip` |
| macOS | `./scripts/build-macos.sh` | `dist/QuakeBundle-macos-x64.zip` or `dist/QuakeBundle-macos-arm64.zip` |

The scripts use `uv` to obtain Python 3.13 and the dependencies listed in
`scripts/requirements-build.txt` in an isolated environment. They then build the
`onefile` executable, test it from a temporary folder without the project files,
and create an archive containing only that executable. The binary is also
available directly in `dist/`. If the build or test fails, the process stops
without publishing a new archive.

You can invoke the scripts from another folder. The package architecture matches
the interpreter used to build it; the scripts do not perform cross-compilation.
On macOS, the output is a standalone binary, not an `.app`.
The end user's computer does not need `uv` or Python.

### Manual builds

Python is only required on the development/build machine (3.10 or newer;
CI uses 3.13). From the repository root:

```sh
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r scripts/requirements-build.txt
python quakebundle.py
python -m PyInstaller --noconfirm scripts/QuakeBundle.spec
```

In **Windows PowerShell**, to use the same Python 3.13 version as CI, install
that version on the build machine and run:

```powershell
py -3.13 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r scripts/requirements-build.txt
.\.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm .\scripts\QuakeBundle.spec
```

Installing PyInstaller alone is not enough: all dependencies must be in the same
environment used for the build. The `.spec` checks their imports and stops the
build if any are missing or cannot be loaded. This Python installation is only
needed for building; the resulting package includes the interpreter and the GUI.

The output is **`dist/QuakeBundle`** on Linux/macOS or **`dist/QuakeBundle.exe`**
on Windows. The `.spec` already defines `onefile` mode; use the same build command
without adding `--onefile`. The build includes Qt plugins through PyInstaller's
hooks.

On macOS, the build produces a standalone Mach-O binary that you can run with
`./dist/QuakeBundle`. It does not produce an `.app`: that format is a directory,
even though Finder displays it as an application. Integration specific to an
`.app` (such as its application metadata) requires distributing that format.

If you previously built the directory format, PyInstaller replaces
`dist/QuakeBundle/` with the executable when rebuilding with `--noconfirm`.
Previously downloaded packages retain their original format.

The **Build Qt6 desktop** workflow builds, verifies, and archives the applications
for Linux x64, Windows x64, macOS Intel, and Apple Silicon. The artifacts are
unsigned packages; the workflow does not publish releases.

On Linux, the executable uses the desktop's `libxkbcommon` and
`libxkbcommon-x11` together. Bundling only one can crash on newer distributions
such as Artix when Qt releases its X11 keyboard state. Both must be installed
on the host (Artix/Arch packages: `libxkbcommon` and `libxkbcommon-x11`). CI also
checks standalone startup and shutdown through X11 under Xvfb.

Standalone does not mean independent of the operating system: Linux needs
Qt-compatible desktop/graphics libraries, SteamCMD's 32-bit dependencies, and
the dependencies required by AppImages; macOS on Apple Silicon requires Rosetta
for SteamCMD. Build on the oldest Linux distribution you want to support;
compatibility with an older glibc is not guaranteed.
Packages for public distribution must include their dependencies' notices and
licenses. Qt/PySide6 are distributed as dynamic libraries.

## Verification

```sh
python -m unittest discover -s tests -v
python quakebundle.py --smoke-test
```

On headless Linux, prefix the command with `QT_QPA_PLATFORM=offscreen`. To save
a window preview: `python quakebundle.py --smoke-test --screenshot preview.png`.
Also check the compiled package:

```sh
QT_QPA_PLATFORM=offscreen dist/QuakeBundle --smoke-test
```

Tests use synthetic processes and files, without credentials or commercial game
downloads. Actual authentication (including Steam Guard and mobile confirmation),
native dependencies, and a real game session require manual validation on each
platform. Cancellation stops managed downloads/processes; copying or extraction
waits until the next safe checkpoint. Closing the window during installation
requests cancellation and waits for the worker thread.

See [tests/README.md](tests/README.md) for coverage of the GUI and internal
installation modules.
