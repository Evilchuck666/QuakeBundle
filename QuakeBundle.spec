# Build on the target OS: python -m PyInstaller --noconfirm QuakeBundle.spec
from pathlib import Path
import importlib
import sys
from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
# PyInstaller can report missing imports as warnings and still produce an EXE.
# Check the build interpreter before touching an existing distribution.
required_modules = ["PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "tqdm", "certifi"]
if sys.platform == "win32":
    required_modules.append("winpty")
dependency_errors = []
for module in required_modules:
    try:
        importlib.import_module(module)
    except (ImportError, OSError) as error:
        dependency_errors.append(f"  {module}: {error}")
if dependency_errors:
    raise SystemExit(
        "Cannot build QuakeBundle: missing or unusable GUI dependencies.\n"
        + "\n".join(dependency_errors)
        + f"\nBuild interpreter: {sys.executable}\n"
        + "Install requirements-build.txt with this same interpreter before building.\n"
        + "Use Python 3.13 to match the tested CI build environment.\n"
        + "See the Windows build commands in README.md."
    )
console_datas, console_binaries, console_imports = (
    collect_all("winpty") if sys.platform == "win32" else ([], [], [])
)
analysis = Analysis(
    [str(root / "quakebundle.py")],
    pathex=[str(root)],
    datas=[(str(root / "assets" / "cfg"), "assets/cfg"),
           (str(root / "assets" / "icons"), "assets/icons"),
           (str(root / "assets" / "links"), "assets/links"),
           (str(root / "README.md"), ".")] + console_datas,
    binaries=console_binaries,
    hiddenimports=["certifi"] + console_imports,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, analysis.binaries, analysis.datas, [],
    name="QuakeBundle", debug=False, strip=False, upx=False,
    console=False, icon=str(root / "assets" / "icons" / "vkQuake.ico"),
)
# One executable on every platform. A macOS .app is a directory bundle,
# so do not wrap this Mach-O executable in BUNDLE.
