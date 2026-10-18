# GUI and installer tests

QuakeBundle has one user interface: the Qt application started by `quakebundle.py`
or the standalone executable. `quake_gui/worker.py` coordinates installation;
`quake_installer/` contains the backend shared by both game editions.

Install the development dependencies and run the offline checks:

```sh
python -m pip install -r requirements-gui.txt
python -m unittest discover -s tests -v
python quakebundle.py --smoke-test
```

On a headless Linux host, set `QT_QPA_PLATFORM=offscreen`. The **Build Qt6 desktop**
workflow runs the tests, builds the standalone application, and checks the package
on Linux x64, Windows x64, macOS Intel, and macOS Apple Silicon.

## Coverage

- Qt tests exercise installation, authentication prompts, cancellation, player
  settings, and launching a native game process through the event loop.
- Installation path tests run the GUI worker for both editions with synthetic
  downloads. They cover absolute, relative, and home-relative destinations,
  cross-filesystem moves, save preservation, SteamCMD reuse, native shortcuts,
  and rejection of destinations inside temporary download directories.
- Backend tests cover game data, nQuake packages, engine selection, downloads,
  retry/completion checks, safe extraction, and platform-specific paths.
- Shortcut tests check quoting, replacement, and recovery on Linux, Windows,
  and macOS. GUI shortcuts launch the engine directly, without system Python.
- Configuration and engine helpers under `assets/scripts/` are imported by the
  GUI. Their tests cover configuration recovery, preservation of custom settings,
  engine arguments, expansions, and platform handling. These modules are bundled
  as internal code; the GUI does not install separate Python launcher scripts.
- Windows authentication tests use real `WriteConsoleW`/`ReadConsoleW` calls to
  exercise password and Steam Guard prompts through ConPTY, plus cancellation
  and secret-free arguments. Ordinary pipes cannot pass the console fixture.
- The Windows package smoke test runs outside the checkout with Python removed
  from `PATH`. It checks the bundled console support with Windows' own `cmd.exe`,
  renders the GUI, and validates resources and configuration helpers.

Tests use temporary files and synthetic processes without Steam credentials or
commercial game downloads. Tests requiring a specific OS are skipped elsewhere.
Actual Steam authentication, mobile approval, native graphics, and a real game
session require manual validation on each supported platform.
