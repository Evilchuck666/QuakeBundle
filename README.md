# QuakeBundle

Instalador y lanzador de Quake con **Qt6 / PySide6**. Una ventana para la campaña
con vkQuake y el multijugador con ezQuake + nQuake, selección de carpeta, ajustes
del jugador, autenticación de Steam y progreso de instalación.

## Aplicación autónoma

El paquete distribuible contiene **Python, Qt6, certificados TLS, iconos y
configuraciones**. El usuario no necesita instalar Python, pip ni Qt. Descomprime
el paquete completo y abre `QuakeBundle` (Linux), `QuakeBundle.exe` (Windows) o
`QuakeBundle.app` (macOS). Conserva toda la carpeta y su directorio `_internal`.

### Descargar para Windows

1. Abre [Build Qt6 desktop en GitHub Actions](https://github.com/Evilchuck666/QuakeBundle/actions/workflows/desktop.yml)
   y selecciona una ejecución completada correctamente.
2. En **Artifacts**, descarga **QuakeBundle-windows-2025** (Windows x64).
   GitHub requiere iniciar sesión para descargar los artefactos.
3. Extrae el ZIP del artefacto y después el archivo
   `QuakeBundle-windows-2025.zip` que contiene.
4. Abre **`QuakeBundle/QuakeBundle.exe`**. El directorio `_internal` debe estar
   junto al ejecutable: incluye el intérprete y todas las dependencias de la GUI.

No necesitas instalar Python, pip ni PySide6, ni ejecutar comandos.
**Code → Download ZIP** descarga el código fuente, no la aplicación autónoma.

El código continúa en Python para reutilizar los instaladores existentes;
PyInstaller incluye su intérprete en la aplicación. Es una distribución autónoma
en una carpeta, no un binario estático universal. Cada sistema y arquitectura
necesita su propia compilación. Véase la
[documentación de PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html).

La aplicación instala y abre los motores directamente, sin necesitar un intérprete
externo para jugar desde la GUI. Los ajustes iniciales, el icono SVG y el
`launcher.py` de cada motor se copian desde los recursos incluidos a la carpeta
de instalación. El lanzador permite abrir el juego fuera de la GUI y requiere
Python instalado para ejecutarlo. Al terminar la instalación, la GUI crea un
acceso directo al motor que funciona sin Python: en el menú de aplicaciones de
Linux, el escritorio de Windows o `~/Applications` en macOS. Se actualiza al
reinstalar. Las configuraciones
existentes se conservan, salvo los campos del jugador que se editan en la ventana.

Para instalar necesitas Internet y una cuenta de Steam que tenga Quake. SteamCMD,
los motores y los datos del juego se descargan durante la instalación; no están
incluidos en el paquete. La contraseña y Steam Guard se solicitan en la ventana
y se envían por la entrada del proceso, nunca como argumentos. QuakeBundle no
los guarda ni registra; SteamCMD puede conservar su propia sesión de Steam.

La GUI conserva SteamCMD entre instalaciones y lo reutiliza para ambas ediciones,
comprobando sus actualizaciones antes de usarlo. Se instala en
`~/.local/share/steamcmd` en Linux (o bajo `XDG_DATA_HOME` si está definido),
`%LOCALAPPDATA%/steamcmd` en Windows y
`~/Library/Application Support/steamcmd` en macOS. Al terminar, la limpieza
elimina los datos temporales descargados del juego y conserva SteamCMD.

En una instalación reconocida, **Jugar** funciona sin autenticarse de nuevo y
permite elegir las expansiones presentes. Los ajustes se guardan en los archivos
del juego. La carpeta y el nombre de usuario se recuerdan con QSettings.

## Desarrollo y compilación

Python solo es necesario en el equipo de desarrollo/compilación (3.10 o superior;
CI utiliza 3.13). Desde la raíz del repositorio:

```sh
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
python quakebundle.py
python -m PyInstaller --noconfirm QuakeBundle.spec
```

La salida está en `dist/QuakeBundle/` o `dist/QuakeBundle.app/`. La compilación
incluye los plugins de Qt mediante los hooks de PyInstaller. El workflow
**Build Qt6 desktop** construye, verifica y archiva las aplicaciones para Linux
x64, Windows x64, macOS Intel y Apple Silicon. Los artefactos son paquetes sin
firma; el workflow no publica releases.

Autónomo no significa independiente del sistema operativo: Linux necesita las
bibliotecas de escritorio/gráficas compatibles con Qt, las dependencias de
SteamCMD de 32 bits y las necesarias para los AppImage; macOS Apple Silicon
requiere Rosetta para SteamCMD. Compila Linux sobre la distribución más antigua
que quieras soportar; no se garantiza compatibilidad con una glibc anterior.
Los paquetes para distribución pública deben incluir los avisos y licencias de
sus dependencias. Qt/PySide6 se distribuyen como bibliotecas dinámicas.

## Verificación

```sh
python -m unittest discover -s tests -v
python quakebundle.py --smoke-test
```

En Linux sin pantalla, antepón `QT_QPA_PLATFORM=offscreen`. Para guardar una
vista de la ventana: `python quakebundle.py --smoke-test --screenshot preview.png`.
Comprueba también el paquete compilado:

```sh
QT_QPA_PLATFORM=offscreen dist/QuakeBundle/QuakeBundle --smoke-test
```

Las pruebas usan procesos y archivos sintéticos, sin credenciales ni descargas
comerciales. La autenticación real (incluidos Steam Guard y la confirmación móvil),
las dependencias nativas y una partida real requieren validación manual en cada
plataforma. Cancelar detiene las descargas/procesos controlados; durante una copia
o extracción se espera al siguiente punto seguro. Cerrar la ventana durante una
instalación solicita cancelación y espera al hilo de trabajo.

Los comandos originales `singleplayer.py` y `multiplayer.py` siguen disponibles;
su uso desde fuentes y sus lanzadores antiguos sí requieren Python. Consulta
[tests/README.md](tests/README.md) para ese flujo.
