# Build on the target OS: python -m PyInstaller --noconfirm QuakeBundle.spec
from pathlib import Path
import sys

root = Path(SPECPATH)
analysis = Analysis(
    [str(root / "quakebundle.py")],
    pathex=[str(root)],
    datas=[(str(root / "assets" / "cfg"), "assets/cfg"),
           (str(root / "assets" / "icons"), "assets/icons"),
           (str(root / "assets" / "links"), "assets/links"),
           (str(root / "assets/scripts/vkQuake/launcher.py"), "assets/scripts/vkQuake"),
           (str(root / "assets/scripts/ezQuake/launcher.py"), "assets/scripts/ezQuake"),
           (str(root / "README.md"), ".")],
    hiddenimports=["certifi"],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True,
    name="QuakeBundle", debug=False, strip=False, upx=False,
    console=False, icon=str(root / "assets" / "icons" / "vkQuake.ico"),
)
collection = COLLECT(exe, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name="QuakeBundle")
if sys.platform == "darwin":
    app = BUNDLE(collection, name="QuakeBundle.app",
                 bundle_identifier="io.quakebundle.desktop",
                 info_plist={"NSHighResolutionCapable": True})
