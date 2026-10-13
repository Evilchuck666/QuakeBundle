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
recovery. Both wizards are integrated into their standalone `launcher.py`;
tests also cover `--setup-only` and running setup through a terminal.
On Linux, launcher tests verify argument forwarding and failure handling;
ezQuake checks that the engine receives its NVIDIA/OpenGL settings.
Engine execution and terminal windows are simulated.
Files and installation directories are temporary; no Steam credentials or
GitHub secrets are needed. Steam, release metadata, Rosetta probes and macOS
DMG commands are simulated. File downloads use local file URLs.

These checks do not download the commercial game, authenticate against Steam,
check live release availability, or launch vkQuake. A complete installation
and gameplay check still requires a Steam account that owns Quake and the
platform's native runtime dependencies.
