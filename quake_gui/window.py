"""The QuakeBundle desktop: installation, Steam authentication and native play."""

from __future__ import annotations

import math
from argparse import ArgumentTypeError
from pathlib import Path

from PySide6.QtCore import (
    QProcess,
    QProcessEnvironment,
    QRegularExpression,
    QSettings,
    QSize,
    Qt,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from quake_installer.config import parse_install_dir
from quake_installer.runtime import external_libraries

from .library import (
    ASSETS,
    EDITIONS,
    configure,
    game_environment,
    installed,
    launch_command,
    read_player,
    validate_player,
)
from .theme import STYLE
from .worker import InstallWorker


class SensitivityValidator(QRegularExpressionValidator):
    def __init__(self, parent=None):
        super().__init__(QRegularExpression(r"[0-9]*(?:\.[0-9]*)?"), parent)

    def validate(self, text, position):
        return super().validate(text.replace(",", "."), position)


def label(text, kind=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if kind:
        widget.setObjectName(kind)
    return widget


class Gate(QWidget):
    """Small procedural Quake-inspired portal, crisp at any display scale."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(200, 120)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(10, 110, 190, 10)
        gradient.setColorAt(0, QColor("#111318"))
        gradient.setColorAt(1, QColor("#583723"))
        painter.fillRect(self.rect(), gradient)
        for index in range(5):
            offset = index * 12
            painter.setPen(QPen(QColor(238, 179, 118, 30 + index * 30), 1.5))
            painter.drawRoundedRect(
                15 + offset,
                8 + offset // 2,
                166 - offset * 2,
                112 - offset,
                54 - offset // 2,
                54 - offset // 2,
            )
        painter.setPen(QPen(QColor("#eeb376"), 2))
        painter.drawLine(99, 60, 99, 104)
        painter.drawEllipse(86, 56, 26, 28)
        painter.end()


class MainWindow(QMainWindow):
    def __init__(self, settings=None):
        super().__init__()
        self.settings = (
            settings
            if settings is not None
            else QSettings("QuakeBundle", "QuakeBundle")
        )
        self.edition = EDITIONS["singleplayer"]
        self.worker = None
        self.game = None
        self.outcome = None
        self.close_pending = False
        self.setWindowTitle("QuakeBundle")
        self.setWindowIcon(QIcon(str(ASSETS / "icons/vkQuake.png")))
        self.resize(1120, 850)
        self.setMinimumSize(920, 700)
        self.setStyleSheet(STYLE)
        self._build()
        key = self.settings.value("edition", "singleplayer")
        self.select_edition(key if key in EDITIONS else "singleplayer")

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(30, 24, 30, 18)
        outer.setSpacing(20)
        header = QHBoxLayout()
        header.addWidget(label("Q  /  QUAKEBUNDLE", "eyebrow"))
        header.addStretch()
        header.addWidget(label("TU PUERTA A QUAKE", "muted"))
        outer.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(24)
        outer.addLayout(columns, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        columns.addWidget(scroll, 1)
        left = QVBoxLayout(content)
        left.setContentsMargins(0, 0, 8, 0)
        left.setSpacing(18)

        hero = QHBoxLayout()
        words = QVBoxLayout()
        words.addWidget(label("EL CLÁSICO. A TU MANERA.", "eyebrow"))
        words.addWidget(label("Vuelve al origen.", "heading"))
        words.addWidget(
            label("Dos formas de jugar. Una sola puerta de entrada.", "muted", True)
        )
        hero.addLayout(words, 1)
        hero.addWidget(Gate())
        left.addLayout(hero)
        left.addWidget(label("01   Elige tu experiencia", "section"))
        editions = QHBoxLayout()
        self.edition_group = QButtonGroup(self)
        self.edition_buttons = {}
        for key, edition in EDITIONS.items():
            button = QPushButton(
                f"{edition.title}\n{edition.engine}\n{edition.description}"
            )
            button.setObjectName("edition")
            button.setCheckable(True)
            button.setMinimumHeight(108)
            button.setIcon(QIcon(str(ASSETS / f"icons/{edition.engine}.png")))
            button.setIconSize(QSize(35, 35))
            button.clicked.connect(
                lambda checked=False, key=key: self.select_edition(key)
            )
            self.edition_group.addButton(button)
            self.edition_buttons[key] = button
            editions.addWidget(button, 1)
        left.addLayout(editions)

        self.form = QFrame()
        self.form.setObjectName("panel")
        form = QVBoxLayout(self.form)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)
        form.addWidget(label("02   Prepara tu partida", "section"))
        form.addWidget(label("Carpeta del juego", "muted"))
        row = QHBoxLayout()
        self.directory = QLineEdit()
        self.directory.setAccessibleName("Carpeta del juego")
        self.directory.editingFinished.connect(self.directory_changed)
        row.addWidget(self.directory, 1)
        browse = QPushButton("Explorar…")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        form.addLayout(row)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.username = QLineEdit(str(self.settings.value("username", "")))
        self.username.setPlaceholderText("Tu cuenta de Steam")
        self.username.setAccessibleName("Usuario de Steam")
        self.username.setToolTip(
            "La cuenta debe tener Quake. La contraseña se solicitará al conectar."
        )
        self.player_name = QLineEdit("Player")
        self.player_name.setMaxLength(64)
        self.player_name.setAccessibleName("Nombre del jugador")
        grid.addWidget(label("Usuario de Steam", "muted"), 0, 0)
        grid.addWidget(label("Nombre del jugador", "muted"), 0, 1)
        grid.addWidget(self.username, 1, 0)
        grid.addWidget(self.player_name, 1, 1)
        form.addLayout(grid)

        advanced = QToolButton()
        advanced.setText("Ajustes del jugador")
        advanced.setCheckable(True)
        advanced.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced.setArrowType(Qt.ArrowType.RightArrow)
        form.addWidget(advanced)
        self.advanced = QWidget()
        self.advanced.setObjectName("transparent")
        options = QGridLayout(self.advanced)
        options.setContentsMargins(0, 0, 0, 0)
        self.sensitivity = QLineEdit("3")
        self.sensitivity.setValidator(SensitivityValidator(self.sensitivity))
        self.sensitivity.setAccessibleName("Sensibilidad del ratón")
        self.team = QLineEdit()
        self.fakename = QLineEdit()
        self.team.setAccessibleName("Equipo")
        self.fakename.setAccessibleName("Alias")
        options.addWidget(label("Sensibilidad", "muted"), 0, 0)
        options.addWidget(self.sensitivity, 1, 0)
        self.team_label = label("Equipo · ezQuake", "muted")
        self.fake_label = label("Alias · ezQuake", "muted")
        options.addWidget(self.team_label, 0, 1)
        options.addWidget(self.team, 1, 1)
        options.addWidget(self.fake_label, 0, 2)
        options.addWidget(self.fakename, 1, 2)
        self.advanced.hide()
        advanced.toggled.connect(self.advanced.setVisible)
        advanced.toggled.connect(
            lambda checked: advanced.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
        )
        form.addWidget(self.advanced)
        form.addWidget(
            label(
                "Necesitas una cuenta de Steam con Quake para instalarlo.",
                "muted",
                True,
            )
        )
        left.addWidget(self.form)

        actions = QHBoxLayout()
        self.install_button = QPushButton("Instalar campaña")
        self.install_button.setObjectName("primary")
        self.install_button.clicked.connect(self.start_install)
        actions.addWidget(self.install_button, 1)
        self.play_button = QPushButton("▶  Jugar")
        self.play_button.clicked.connect(self.play)
        actions.addWidget(self.play_button)
        left.addLayout(actions)
        self.mods = QComboBox()
        self.mods.setAccessibleName("Juego o expansión")
        left.addWidget(self.mods)
        self.hint = label("", "muted", True)
        left.addWidget(self.hint)
        left.addStretch()

        side = QFrame()
        side.setObjectName("panel")
        side.setFixedWidth(285)
        columns.addWidget(side)
        panel = QVBoxLayout(side)
        panel.setContentsMargins(20, 22, 20, 20)
        panel.setSpacing(15)
        panel.addWidget(label("CENTRO DE ACTIVIDAD", "eyebrow"))
        self.activity = label("Todo listo para empezar", "section", True)
        panel.addWidget(self.activity)
        self.stage_labels = []
        for title in (
            "Preparar SteamCMD",
            "Conectar con Steam",
            "Descargar Quake",
            "Instalar el juego",
            "Instalar el motor",
            "Configurar y terminar",
        ):
            item = label("○   " + title, "muted", True)
            self.stage_labels.append(item)
            panel.addWidget(item)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        panel.addWidget(self.progress)
        self.progress_text = label("Esperando instalación", "muted", True)
        panel.addWidget(self.progress_text)

        self.auth = QWidget()
        self.auth.setObjectName("transparent")
        auth = QVBoxLayout(self.auth)
        auth.setContentsMargins(0, 0, 0, 0)
        self.auth_label = label("", "section", True)
        auth.addWidget(self.auth_label)
        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setAccessibleName("Respuesta de autenticación de Steam")
        self.secret.returnPressed.connect(self.send_secret)
        auth.addWidget(self.secret)
        send = QPushButton("Continuar")
        send.clicked.connect(self.send_secret)
        auth.addWidget(send)
        auth.addWidget(
            label(
                "Se envía a SteamCMD. QuakeBundle no guarda esta respuesta.",
                "muted",
                True,
            )
        )
        panel.addWidget(self.auth)
        self.auth.hide()
        self.cancel_button = QPushButton("Cancelar instalación")
        self.cancel_button.clicked.connect(self.cancel_install)
        self.cancel_button.hide()
        panel.addWidget(self.cancel_button)
        panel.addStretch()
        details = QToolButton()
        details.setText("Ver registro de actividad")
        details.setCheckable(True)
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.logs.setMaximumHeight(170)
        self.logs.hide()
        details.toggled.connect(self.logs.setVisible)
        panel.addWidget(details)
        panel.addWidget(self.logs)
        open_folder = QPushButton("Abrir carpeta del juego")
        open_folder.clicked.connect(self.open_folder)
        panel.addWidget(open_folder)

        footer = QHBoxLayout()
        footer.addWidget(label("QUAKE  /  1996 → HOY", "muted"))
        footer.addStretch()
        footer.addWidget(label("Qt6  ·  vkQuake + ezQuake", "muted"))
        outer.addLayout(footer)

    def select_edition(self, key):
        if self.worker or self.game:
            return
        self.edition = EDITIONS[key]
        self.edition_buttons[key].setChecked(True)
        self.settings.setValue("edition", key)
        self.directory.setText(
            str(self.settings.value(f"directory/{key}", str(self.edition.default_dir)))
        )
        for widget in (self.team, self.fakename, self.team_label, self.fake_label):
            widget.setVisible(key == "multiplayer")
        self.directory_changed()

    def root_path(self):
        return parse_install_dir(self.directory.text().strip())

    def directory_changed(self):
        try:
            root = self.root_path()
            self.settings.setValue(f"directory/{self.edition.key}", str(root))
            player = read_player(self.edition, root)
            self.player_name.setText(player["name"] or "Player")
            sensitivity = float(player["sensitivity"] or 3)
            sensitivity = (
                min(1000, max(0.01, sensitivity)) if math.isfinite(sensitivity) else 3
            )
            self.sensitivity.setText(str(sensitivity))
            self.team.setText(player["team"])
            self.fakename.setText(player["cl_fakename"])
        except (OSError, ValueError, ArgumentTypeError) as error:
            self.refresh()
            self.hint.setText(str(error))
            return
        self.refresh()

    def refresh(self):
        ready = False
        path_error = None
        self.mods.clear()
        self.mods.addItem("Quake · juego original", "")
        try:
            root = self.root_path()
            ready = installed(self.edition, root)
            for folder, title in (
                ("hipnotic", "Scourge of Armagon"),
                ("rogue", "Dissolution of Eternity"),
                ("dopa", "Dimension of the Past"),
                ("mg1", "Dimension of the Machine"),
                ("mg3", "Dawn of the Machine"),
                ("ctf", "Capture the Flag"),
                ("arena", "Clan Arena"),
                ("fortress", "Team Fortress"),
            ):
                if (root / folder).is_dir():
                    self.mods.addItem(title, folder)
        except (OSError, ValueError, ArgumentTypeError) as error:
            path_error = str(error)
        busy = self.worker is not None or self.game is not None
        self.form.setEnabled(not busy)
        for button in self.edition_buttons.values():
            button.setEnabled(not busy)
        self.install_button.setEnabled(not busy and path_error is None)
        self.install_button.setText(
            "Reinstalar / actualizar"
            if ready
            else f"Instalar {self.edition.title.lower()}"
        )
        self.play_button.setEnabled(ready and not busy)
        self.mods.setEnabled(ready and not busy)
        self.mods.setVisible(ready)
        self.hint.setText(
            "Instalado. Puedes jugar o actualizar el motor y los archivos del juego."
            if ready
            else "Se descargarán el juego y su motor. Tus partidas y ajustes existentes se conservan."
        )
        if path_error:
            self.hint.setText(path_error)

    def browse(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Elige la carpeta del juego", self.directory.text()
        )
        if directory:
            self.directory.setText(directory)
            self.directory_changed()

    def player(self):
        try:
            sensitivity = float(self.sensitivity.text().strip().replace(",", "."))
        except ValueError:
            raise ValueError(
                "Escribe una sensibilidad numérica entre 0,01 y 1000."
            ) from None
        if not math.isfinite(sensitivity) or not 0.01 <= sensitivity <= 1000:
            raise ValueError("La sensibilidad debe estar entre 0,01 y 1000.")
        data = {
            "name": self.player_name.text().strip(),
            "sensitivity": sensitivity,
            "team": self.team.text(),
            "fakename": self.fakename.text(),
        }
        validate_player(**data)
        return data

    def start_install(self):
        try:
            root = self.root_path()
            username = self.username.text().strip()
            if (
                not username
                or username.startswith(("+", "-"))
                or any(char.isspace() or ord(char) < 32 for char in username)
            ):
                raise ValueError(
                    "Escribe el nombre de inicio de sesión de Steam, sin espacios."
                )
            other = EDITIONS[
                "multiplayer" if self.edition.key == "singleplayer" else "singleplayer"
            ]
            other_root = (
                Path(
                    str(
                        self.settings.value(
                            f"directory/{other.key}", str(other.default_dir)
                        )
                    )
                )
                .expanduser()
                .resolve()
            )
            if (
                root == other_root
                or root.is_relative_to(other_root)
                or other_root.is_relative_to(root)
            ):
                raise ValueError(
                    "Usa carpetas separadas para campaña y multijugador, sin anidarlas."
                )
            if installed(other, root):
                raise ValueError(
                    "Esta carpeta contiene la otra edición. Elige una carpeta diferente."
                )
            player = self.player()
        except (OSError, ValueError, ArgumentTypeError) as error:
            self.error(str(error))
            return
        self.settings.setValue("username", username)
        self.settings.setValue(f"directory/{self.edition.key}", str(root))
        self.logs.clear()
        self.outcome = None
        worker = InstallWorker(self.edition, root, username, player, self)
        self.worker = worker
        worker.stage.connect(self.set_stage)
        worker.progress.connect(self.set_progress)
        worker.log.connect(self.logs.appendPlainText)
        worker.challenge.connect(self.ask_secret)
        worker.status.connect(self.activity.setText)
        worker.failed.connect(self.install_error)
        worker.completed.connect(self.install_outcome)
        worker.finished.connect(self.install_finished)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancelar instalación")
        self.cancel_button.show()
        self.refresh()
        worker.start()

    def set_stage(self, number, title):
        self.activity.setText(title)
        self.progress.setRange(0, 0)
        self.progress_text.setText(f"Paso {number + 1} de 6")
        for index, item in enumerate(self.stage_labels):
            text = item.text()[4:]
            item.setText(
                ("✓   " if index < number else "●   " if index == number else "○   ")
                + text
            )
            item.setStyleSheet(
                f"color: {'#eeb376' if index == number else '#9bd0b1' if index < number else '#969ca9'};"
            )

    def set_progress(self, value, total, description):
        if total and total > 0:
            percent = min(100, max(0, round(value / total * 100)))
            self.progress.setRange(0, 100)
            self.progress.setValue(percent)
            self.progress_text.setText(f"{description or 'Descargando'} · {percent}%")
        else:
            self.progress.setRange(0, 0)
            self.progress_text.setText(description or "Descargando…")

    def ask_secret(self, prompt):
        self.auth_label.setText(prompt)
        self.secret.clear()
        self.auth.show()
        self.secret.setFocus()
        self.activity.setText("Steam necesita tu respuesta")

    def send_secret(self):
        if self.worker and self.secret.text():
            self.worker.answer(self.secret.text())
            self.secret.clear()
            self.auth.hide()
            self.activity.setText("Verificando con Steam…")

    def cancel_install(self):
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("Cancelando…")
            self.activity.setText("Terminando la operación actual…")
            self.auth.hide()
            self.secret.clear()

    def install_error(self, message):
        self.logs.appendPlainText(message)
        self.error(message)

    def install_outcome(self, outcome):
        self.outcome = outcome

    def install_finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.auth.hide()
        self.secret.clear()
        self.cancel_button.hide()
        self.progress.setRange(0, 100)
        if self.outcome == "success":
            self.progress.setValue(100)
            self.activity.setText("Tu próxima partida te espera")
            self.progress_text.setText("Instalación completada")
            for item in self.stage_labels:
                item.setText("✓   " + item.text()[4:])
                item.setStyleSheet("color: #9bd0b1;")
        else:
            self.progress.setValue(0)
            self.activity.setText(
                "Instalación cancelada"
                if self.outcome == "cancelled"
                else "No se pudo completar"
            )
            self.progress_text.setText("Puedes volver a intentarlo.")
        self.refresh()
        if self.close_pending:
            self.close()

    def play(self):
        try:
            root = self.root_path()
            command = launch_command(self.edition, root, self.mods.currentData())
            configure(self.edition, root, **self.player())
            process = QProcess(self)
            process.setWorkingDirectory(str(root))
            environment = QProcessEnvironment()
            for key, value in game_environment(self.edition).items():
                environment.insert(key, value)
            process.setProcessEnvironment(environment)
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.readyReadStandardOutput.connect(
                lambda: self.logs.appendPlainText(
                    bytes(process.readAllStandardOutput())
                    .decode(errors="replace")
                    .strip()
                )
            )
            process.finished.connect(self.game_finished)
            process.errorOccurred.connect(self.game_error)
            self.game = process
            with external_libraries():
                process.start(command[0], command[1:])
                process.waitForStarted(3000)
            if self.game:
                self.activity.setText(f"{self.edition.engine} en ejecución")
            self.refresh()
        except (OSError, ValueError, ArgumentTypeError) as error:
            self.error(str(error))

    def game_error(self, error):
        if self.game and error == QProcess.ProcessError.FailedToStart:
            self.error(self.game.errorString())
            self.game_finished(1)

    def game_finished(self, code, status=None):
        if self.game:
            self.game.deleteLater()
            self.game = None
        self.activity.setText(
            "Listo para otra partida"
            if code == 0
            else "El motor se ha cerrado con un error"
        )
        if code != 0:
            self.logs.appendPlainText(
                f"El motor terminó con el código {code}. Comprueba el registro y las dependencias gráficas."
            )
            self.logs.show()
        self.refresh()

    def open_folder(self):
        try:
            root = self.root_path()
            if not root.is_dir():
                raise ValueError(
                    "La carpeta todavía no existe. Instala el juego o elige una carpeta existente."
                )
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
        except (OSError, ValueError, ArgumentTypeError) as error:
            self.error(str(error))

    def error(self, message):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("QuakeBundle")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(message)
        box.exec()

    def closeEvent(self, event):
        if self.worker:
            self.close_pending = True
            self.cancel_install()
            event.ignore()
        elif self.game:
            self.activity.setText("Cierra el juego antes de salir de QuakeBundle.")
            event.ignore()
        else:
            self.settings.sync()
            event.accept()
