#!/usr/bin/env python3
"""Descarga Quake y sus expansiones con steamcmd en ~/.local/share/quake/singleplayer.

Uso:
    python3 download_quake.py USUARIO_STEAM

Quake y sus expansiones no son gratuitos, así que hace falta una cuenta de Steam
que los posea.
steamcmd pedirá la contraseña y el código de Steam Guard de forma interactiva.
"""

import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

APP_IDS = [
    2310,  # Quake
    9030,  # Quake Mission Pack 1: Scourge of Armagon
    9040,  # Quake Mission Pack 2: Dissolution of Eternity
]
BASE_DIR = Path.home() / ".local" / "share" / "quake"
INSTALL_DIR = BASE_DIR / "singleplayer"
STEAMCMD_DIR = BASE_DIR / "steamcmd"
# steamcmd crea dentro del directorio de instalación carpetas que no forman
# parte de la instalación de Steam: "steamapps/" (manifiestos de las apps) y
# "_CommonRedist/" (redistribuibles de DirectX/VC++). Las guardamos aquí entre
# ejecuciones para que el árbol final quede limpio y las siguientes ejecuciones
# sean actualizaciones incrementales.
STEAMCMD_ONLY_DIRS = {
    "steamapps": STEAMCMD_DIR / "quake-steamapps",
    "_CommonRedist": STEAMCMD_DIR / "quake-CommonRedist",
}

STEAMCMD_URLS = {
    "Linux": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz",
    "Darwin": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_osx.tar.gz",
    "Windows": "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip",
}

# Ficheros que deben existir tras una descarga correcta.
EXPECTED_FILES = [
    "quake.exe",
    "Winquake.exe",
    "id1/PAK0.PAK",
    "id1/PAK1.PAK",
    "hipnotic/pak0.pak",  # 9030
    "rogue/pak0.pak",  # 9040
    "rerelease/quake_x64_steam.exe",
    "rerelease/id1/pak0.pak",
]


def steamcmd_executable() -> Path:
    system = platform.system()
    if system == "Windows":
        return STEAMCMD_DIR / "steamcmd.exe"
    return STEAMCMD_DIR / "steamcmd.sh"


def install_steamcmd() -> Path:
    exe = steamcmd_executable()
    if exe.exists():
        print(f"steamcmd ya está instalado en {STEAMCMD_DIR}")
        return exe

    system = platform.system()
    url = STEAMCMD_URLS.get(system)
    if url is None:
        sys.exit(f"Sistema operativo no soportado: {system}")

    print(f"Descargando steamcmd para {system} desde {url}")
    with urllib.request.urlopen(url) as response:
        data = response.read()

    STEAMCMD_DIR.mkdir(parents=True, exist_ok=True)
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(STEAMCMD_DIR)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            archive.extractall(STEAMCMD_DIR, filter="tar")

    if not exe.exists():
        sys.exit(f"No se encontró {exe} tras extraer steamcmd")
    if system != "Windows":
        exe.chmod(0o755)
    return exe


def restore_steamcmd_dirs() -> None:
    for name, cache in STEAMCMD_ONLY_DIRS.items():
        if cache.exists() and not (INSTALL_DIR / name).exists():
            shutil.move(cache, INSTALL_DIR / name)


def stash_steamcmd_dirs() -> None:
    for name, cache in STEAMCMD_ONLY_DIRS.items():
        path = INSTALL_DIR / name
        if path.exists():
            shutil.rmtree(cache, ignore_errors=True)
            shutil.move(path, cache)


def download_quake(steamcmd: Path, username: str, attempts: int) -> None:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        str(steamcmd),
        # Quake solo tiene depots de Windows; sin esto steamcmd en Linux/macOS
        # no descargaría nada.
        "+@sSteamCmdForcePlatformType", "windows",
        # force_install_dir tiene que ir antes de +login.
        "+force_install_dir", str(INSTALL_DIR),
        "+login", username,
    ]
    # Las expansiones se instalan en el mismo directorio que Quake.
    for app_id in APP_IDS:
        command += ["+app_update", str(app_id), "validate"]
    command.append("+quit")

    restore_steamcmd_dirs()
    try:
        # La primera ejecución de steamcmd se autoactualiza y a veces falla el
        # app_update; reintentar es lo habitual.
        for attempt in range(1, attempts + 1):
            print(f"Ejecutando steamcmd (intento {attempt}/{attempts})")
            result = subprocess.run(command, cwd=STEAMCMD_DIR)
            if result.returncode == 0:
                return
            print(f"steamcmd terminó con código {result.returncode}")
        sys.exit("No se pudo descargar Quake y sus expansiones con steamcmd")
    finally:
        stash_steamcmd_dirs()


def verify() -> None:
    missing = [f for f in EXPECTED_FILES if not (INSTALL_DIR / f).exists()]
    if missing:
        sys.exit("Faltan ficheros tras la descarga:\n  " + "\n  ".join(missing))
    print(f"Quake descargado correctamente en {INSTALL_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "username",
        nargs="?",
        default=os.environ.get("STEAM_USERNAME"),
        help="usuario de Steam que posee Quake y sus expansiones (o variable STEAM_USERNAME)",
    )
    parser.add_argument("--attempts", type=int, default=3, help="reintentos de steamcmd")
    args = parser.parse_args()
    if not args.username:
        parser.error("hace falta un usuario de Steam (argumento o STEAM_USERNAME)")

    steamcmd = install_steamcmd()
    download_quake(steamcmd, args.username, args.attempts)
    verify()


if __name__ == "__main__":
    main()
