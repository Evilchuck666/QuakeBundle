"""Create per-user launchers using the desktop facilities of each platform."""

from __future__ import annotations

import base64
import plistlib
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .runtime import external_environment, external_libraries


@dataclass(frozen=True)
class Shortcut:
    """Engine-specific names and assets shared by the platform helpers."""

    engine: str
    name: str
    icon: str
    bundle_id: str


VKQUAKE_SHORTCUT = Shortcut(
    engine="vkQuake",
    name="Quake Single Player (vkQuake Port)",
    icon="vkQuake",
    bundle_id="com.quakebundle.singleplayer",
)
EZQUAKE_SHORTCUT = Shortcut(
    engine="ezQuake",
    name="Quake Multiplayer (ezQuake Port)",
    icon="ezQuake",
    bundle_id="com.quakebundle.multiplayer",
)
AUTOMATOR_STUB = Path(
    "/System/Library/CoreServices/Automator Application Stub.app/Contents/MacOS/"
    "Automator Application Stub"
)


def desktop_value(value):
    """Escape a Desktop Entry string before its key-specific interpretation."""
    return (str(value).replace("\\", "\\\\").replace("\n", "\\n")
            .replace("\r", "\\r").replace("\t", "\\t"))


def desktop_argument(value):
    """Quote one Exec argument, including literal percent signs and backslashes."""
    value = str(value).replace("%", "%%")
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, "\\" + character)
    return desktop_value('"' + value + '"')


def create_linux_shortcut(root, python, fetch, shortcut: Shortcut, *, command=None):
    """Download and customize the template, then atomically publish it."""
    directory = Path.home() / ".local/share/applications"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{shortcut.engine}.desktop"
    url = ("https://raw.githubusercontent.com/Evilchuck666/QuakeBundle/main/"
           f"assets/links/{destination.name}")
    with tempfile.TemporaryDirectory(prefix=".quake-shortcut-", dir=directory) as tmp:
        staged = Path(tmp) / destination.name
        fetch(url, staged)
        replacements = {
            "Name": desktop_value(shortcut.name),
            "Comment": desktop_value(f"Play Quake with {shortcut.engine}"),
            "Exec": " ".join(desktop_argument(arg) for arg in (
                command if command is not None else [python, root / "launcher.py"]
            )),
            "TryExec": desktop_value(command[0] if command is not None else python),
            "Path": desktop_value(root),
            "Icon": desktop_value(root / f"{shortcut.icon}.svg"),
            "Terminal": "false" if command is not None else "true",
        }
        lines, found, section = [], set(), ""
        for line in staged.read_text(encoding="utf-8").splitlines():
            if line.startswith("["):
                section = line
            key = line.partition("=")[0]
            if section == "[Desktop Entry]" and key in replacements:
                if key in found:
                    raise OSError(f"Duplicate {key} in {destination.name} template")
                found.add(key)
                line = f"{key}={replacements[key]}"
            lines.append(line)
        if found != replacements.keys():
            raise OSError(f"Incomplete {destination.name} template")
        staged.write_text("\n".join(lines) + "\n", encoding="utf-8")
        staged.chmod(0o644)
        staged.replace(destination)
    return destination


WINDOWS_SHORTCUT_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$shell = New-Object -ComObject WScript.Shell
$desktop = $shell.SpecialFolders.Item('Desktop')
if (-not $desktop) { throw 'Could not locate the user Desktop' }
$destination = Join-Path $desktop ($env:QUAKE_SHORTCUT_NAME + '.lnk')
$staged = Join-Path $desktop (([guid]::NewGuid().ToString()) + '.lnk')
try {
    $link = $shell.CreateShortcut($staged)
    $link.TargetPath = Join-Path $env:QUAKE_SHORTCUT_ROOT 'launcher.exe'
    if ($env:QUAKE_SHORTCUT_TARGET) {
        $link.TargetPath = $env:QUAKE_SHORTCUT_TARGET
        $link.Arguments = $env:QUAKE_SHORTCUT_ARGUMENTS
    }
    $link.WorkingDirectory = $env:QUAKE_SHORTCUT_ROOT
    $link.IconLocation = $env:QUAKE_SHORTCUT_ICON + ',0'
    $link.Description = $env:QUAKE_SHORTCUT_NAME
    $link.Save()
    if (Test-Path -LiteralPath $destination) {
        # PowerShell converts $null to an empty string for .NET string arguments.
        [System.IO.File]::Replace($staged, $destination, [System.Management.Automation.Language.NullString]::Value)
    } else {
        [System.IO.File]::Move($staged, $destination)
    }
    [Console]::WriteLine($destination)
} finally {
    if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged }
}
'''


def run_native(command, **kwargs):
    """Surface native tool errors with their diagnostic output."""
    try:
        kwargs.setdefault("env", external_environment())
        with external_libraries():
            return subprocess.run(command, check=True, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", **kwargs)
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        raise OSError(f"{command[0]} failed: {detail}") from error


def create_windows_shortcut(root, shortcut: Shortcut, *, command=None):
    """Use Windows' actual Desktop location, including redirected desktops."""
    encoded = base64.b64encode(WINDOWS_SHORTCUT_SCRIPT.encode("utf-16le")).decode("ascii")
    result = run_native(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        env=dict(external_environment(), QUAKE_SHORTCUT_ROOT=str(root),
                 QUAKE_SHORTCUT_NAME=shortcut.name,
                 QUAKE_SHORTCUT_ICON=str(root / f"{shortcut.icon}.ico"),
                 QUAKE_SHORTCUT_TARGET=str(command[0]) if command is not None else "",
                 QUAKE_SHORTCUT_ARGUMENTS=subprocess.list2cmdline(command[1:])
                 if command is not None else ""),
    )
    destination = Path(result.stdout.strip())
    if not result.stdout.strip() or not destination.is_file():
        raise OSError("Windows did not create the launcher shortcut")
    return destination


MACOS_VALIDATE_AND_ICON = '''
ObjC.import('Automator');
ObjC.import('AppKit');
function run(argv) {
    var error = Ref();
    var workflow = $.AMWorkflow.alloc.initWithContentsOfURLError(
        $.NSURL.fileURLWithPath(argv[0] + '/Contents/document.wflow'), error);
    if (workflow.isNil()) {
        throw Error('Invalid Automator workflow: ' + ObjC.unwrap(error[0].localizedDescription));
    }
    if (Number(workflow.actions.count) !== 1) {
        throw Error('Automator could not load the launcher action');
    }
    var icon = $.NSImage.alloc.initWithContentsOfFile(argv[1]);
    if (icon.isNil() || !$.NSWorkspace.sharedWorkspace.setIconForFileOptions(icon, argv[0], 0)) {
        throw Error('Could not apply the PNG shortcut icon');
    }
}
'''


def build_macos_workflow(root, python, *, command=None):
    """Describe the Automator action that runs the installed Python launcher."""
    script = "cd " + shlex.quote(str(root)) + "\nexec " + shlex.join(
        command if command is not None else [str(python), str(root / "launcher.py")]
    ) + "\n"
    return {
        "AMDocumentVersion": "2",
        "actions": [{"action": {
            "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
            "ActionName": "Run Shell Script",
            "BundleIdentifier": "com.apple.RunShellScript",
            "ActionParameters": {
                "shell": "/bin/sh", "source": script,
                "inputMethod": 0, "CheckedForUserDefaultShell": True,
            },
        }}],
        "connectors": [],
        "workflowMetaData": {"workflowTypeIdentifier": "com.apple.Automator.application"},
    }


def build_macos_application(staged, root, python, shortcut: Shortcut, *, command=None):
    """Write the Automator executable, application metadata and workflow."""
    contents = staged / "Contents"
    executable = contents / "MacOS/Automator Application Stub"
    executable.parent.mkdir(parents=True)
    shutil.copyfile(AUTOMATOR_STUB, executable)
    executable.chmod(0o755)
    info = {
        "CFBundleExecutable": executable.name,
        "CFBundleIdentifier": shortcut.bundle_id,
        "CFBundleName": shortcut.name,
        "CFBundleDisplayName": shortcut.name,
        "CFBundlePackageType": "APPL",
        "CFBundleVersion": "1",
        "LSUIElement": True,
    }
    for name, data in (("Info.plist", info),
                       ("document.wflow", build_macos_workflow(root, python, command=command))):
        with (contents / name).open("wb") as output:
            plistlib.dump(data, output)


def publish_macos_application(staged, destination):
    """Replace the application, restoring the previous version if publication fails."""
    # A nonempty .app directory cannot be replaced with a single rename.
    backup = staged.parent / "previous.app"
    had_previous = destination.exists() or destination.is_symlink()
    if had_previous:
        destination.rename(backup)
    try:
        staged.rename(destination)
    except OSError:
        if had_previous:
            backup.rename(destination)
        raise


def create_macos_shortcut(root, python, shortcut: Shortcut, *, command=None):
    """Prepare, validate and publish an Automator application with its PNG icon."""
    directory = Path.home() / "Applications"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (shortcut.name + ".app")
    with tempfile.TemporaryDirectory(prefix=".quake-shortcut-", dir=directory) as tmp:
        staged = Path(tmp) / destination.name
        build_macos_application(staged, root, python, shortcut, command=command)
        run_native(["/usr/bin/osascript", "-l", "JavaScript", "-e", MACOS_VALIDATE_AND_ICON,
                    str(staged), str(root / f"{shortcut.icon}.png")])
        publish_macos_application(staged, destination)
    return destination
