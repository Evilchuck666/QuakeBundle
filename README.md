# QuakeBundle

Instalador y lanzador de Quake con **Qt6 / PySide6**. Una ventana para la campaña
con vkQuake y el multijugador con ezQuake + nQuake, selección de carpeta, ajustes
del jugador, autenticación de Steam y progreso de instalación.

## Aplicación autónoma

El paquete distribuible contiene **Python, Qt6, certificados TLS, iconos y
configuraciones en un único ejecutable**. El usuario no necesita instalar
Python, pip ni Qt. Descomprime y abre `QuakeBundle` (Linux/macOS) o
`QuakeBundle.exe` (Windows). No necesita un directorio `_internal` al lado.

### Descargar para Windows

1. Abre [Build Qt6 desktop en GitHub Actions](https://github.com/Evilchuck666/QuakeBundle/actions/workflows/desktop.yml)
   y selecciona una ejecución completada correctamente.
2. En **Artifacts**, descarga **QuakeBundle-windows-2025** (Windows x64).
   GitHub requiere iniciar sesión para descargar los artefactos.
3. Extrae el ZIP del artefacto y después el archivo
   `QuakeBundle-windows-2025.zip` que contiene.
4. Abre **`QuakeBundle.exe`**. Es el único archivo necesario para ejecutar la GUI.

No necesitas instalar Python, pip ni PySide6, ni ejecutar comandos.
**Code → Download ZIP** descarga el código fuente, no la aplicación autónoma.

La GUI y su lógica de instalación están escritas en Python;
PyInstaller incluye su intérprete en la aplicación mediante el modo `onefile`.
Al arrancar extrae las bibliotecas y recursos a una carpeta temporal, que elimina
al cerrar normalmente. Esto añade tiempo al arranque y requiere espacio temporal;
en Linux esa ubicación debe permitir ejecutar las bibliotecas extraídas.
Es un ejecutable autoextraíble, no un AppImage ni un binario estático universal.
Cada sistema y arquitectura necesita su propia compilación. Véase la
[documentación de PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html).

La aplicación instala y abre los motores directamente, sin necesitar un intérprete
externo para jugar desde la GUI. Los ajustes iniciales y los iconos se copian
desde los recursos incluidos a la carpeta de instalación. La GUI es la única
interfaz del instalador. Al terminar la instalación, crea un
acceso directo al motor que funciona sin Python: en el menú de aplicaciones de
Linux, el escritorio de Windows o `~/Applications` en macOS. Se actualiza al
reinstalar. Las configuraciones
existentes se conservan, salvo los campos del jugador que se editan en la ventana.

Para instalar necesitas Internet y una cuenta de Steam que tenga Quake. SteamCMD,
los motores y los datos del juego se descargan durante la instalación; no están
incluidos en el paquete. La contraseña y Steam Guard se solicitan en la ventana
y se envían por la entrada del proceso, nunca como argumentos. QuakeBundle no
los guarda ni registra; SteamCMD puede conservar su propia sesión de Steam.
En Windows, el inicio de sesión usa una consola virtual oculta (ConPTY) para
recibir las peticiones de contraseña y Steam Guard. Su soporte está incluido
en el paquete autónomo; no requiere instalar Python ni componentes adicionales.

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

En **Windows PowerShell**, para utilizar el mismo Python 3.13 que CI, instala
esa versión en el equipo de compilación y ejecuta:

```powershell
py -3.13 -m venv .venv-build
.\.venv-build\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm .\QuakeBundle.spec
```

Instalar solo PyInstaller no basta: todas las dependencias deben estar en el
mismo entorno desde el que se compila. El `.spec` comprueba sus importaciones
y detiene la compilación si faltan o no se pueden cargar. Este Python solo se
necesita para construir; el paquete resultante incluye el intérprete y la GUI.

La salida es **`dist/QuakeBundle`** en Linux/macOS o **`dist/QuakeBundle.exe`**
en Windows. El modo `onefile` ya está definido en el `.spec`; se utiliza el mismo
comando de compilación, sin añadir `--onefile`. La compilación incluye los plugins
de Qt mediante los hooks de PyInstaller.

En macOS se genera un binario Mach-O independiente que puedes ejecutar con
`./dist/QuakeBundle`. No se genera un `.app`: ese formato es una carpeta, aunque
Finder lo muestre como una aplicación. La integración propia de un `.app`
(por ejemplo, su metadato de aplicación) requiere distribuir ese formato.

Si antes compilaste el formato de carpeta, PyInstaller reemplaza
`dist/QuakeBundle/` por el ejecutable al reconstruir con `--noconfirm`.
Los paquetes antiguos descargados siguen teniendo su formato anterior.

El workflow
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
QT_QPA_PLATFORM=offscreen dist/QuakeBundle --smoke-test
```

Las pruebas usan procesos y archivos sintéticos, sin credenciales ni descargas
comerciales. La autenticación real (incluidos Steam Guard y la confirmación móvil),
las dependencias nativas y una partida real requieren validación manual en cada
plataforma. Cancelar detiene las descargas/procesos controlados; durante una copia
o extracción se espera al siguiente punto seguro. Cerrar la ventana durante una
instalación solicita cancelación y espera al hilo de trabajo.

Consulta [tests/README.md](tests/README.md) para conocer la cobertura de la GUI
y de los módulos internos de instalación.
