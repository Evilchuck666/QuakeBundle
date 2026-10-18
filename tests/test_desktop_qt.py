"""Qt event-loop and installer integration checks with synthetic Steam/game data."""

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = importlib.util.find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QSettings, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from quake_gui.library import ASSETS, EDITIONS, configure
    from quake_gui.window import MainWindow
    from quake_gui.worker import InstallWorker
    from quake_installer import (
        ezquake,
        gamedata,
        interactive,
        shortcuts,
        steamcmd,
        vkquake,
    )


@unittest.skipUnless(HAS_QT, "Install scripts/requirements-gui.txt to run Qt tests")
class DesktopQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(
            self.stack.enter_context(tempfile.TemporaryDirectory(prefix="qt quake "))
        ).resolve()

    def pump_until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(condition(), "The Qt operation did not finish")

    def window(self):
        settings = QSettings(
            str(self.root / "settings.ini"), QSettings.Format.IniFormat
        )
        for key in EDITIONS:
            settings.setValue(f"directory/{key}", str(self.root / key))
        window = MainWindow(settings)
        self.addCleanup(window.deleteLater)
        return window

    def test_sensitivity_can_be_typed_and_saved_with_either_decimal_separator(self):
        window = self.window()
        window.advanced.show()
        window.show()
        self.app.processEvents()
        for text in ("3,50", "2.75", "0.01", "1000"):
            with self.subTest(text=text):
                QTest.mouseClick(window.sensitivity, Qt.MouseButton.LeftButton)
                QTest.keyClick(
                    window.sensitivity,
                    Qt.Key.Key_A,
                    Qt.KeyboardModifier.ControlModifier,
                )
                QTest.keyClick(window.sensitivity, Qt.Key.Key_Backspace)
                self.assertEqual(window.sensitivity.text(), "")
                QTest.keyClicks(window.sensitivity, text)
                QTest.keyClick(window.sensitivity, Qt.Key.Key_Tab)
                self.assertEqual(window.sensitivity.text(), text.replace(",", "."))
                player = window.player()
                expected = float(text.replace(",", "."))
                self.assertEqual(player["sensitivity"], expected)
                configure(window.edition, window.root_path(), **player)
                window.directory_changed()
                self.assertEqual(window.player()["sensitivity"], expected)
        window.close()

    def test_sensitivity_normalizes_paste_and_rejects_invalid_characters(self):
        window = self.window()
        field = window.sensitivity
        field.clear()
        QTest.keyClicks(field, "1a -+e/2")
        self.assertEqual(field.text(), "12")
        field.setCursorPosition(1)
        QTest.keyClicks(field, ",")
        self.assertEqual(field.text(), "1.2")
        self.assertEqual(field.cursorPosition(), 2)
        QTest.keyClicks(field, ".,3")
        self.assertEqual(field.text(), "1.32")
        clipboard = self.app.clipboard()
        original = clipboard.text()
        try:
            field.selectAll()
            clipboard.setText("4,25")
            field.paste()
            self.assertEqual(field.text(), "4.25")
            for text in ("abc", "1.2.3", "1,2.3", "-3", "1e2", " 3 "):
                with self.subTest(text=text):
                    field.selectAll()
                    clipboard.setText(text)
                    field.paste()
                    self.assertEqual(field.text(), "4.25")
        finally:
            clipboard.setText(original)

    def test_invalid_sensitivity_prevents_installation(self):
        window = self.window()
        window.username.setText("account")
        for text in ("", "abc", "0", "-1", "0.001", "1001", "nan", "inf"):
            with self.subTest(text=text), patch.object(window, "error") as error:
                window.sensitivity.setText(text)
                window.start_install()
                self.assertIsNone(window.worker)
                error.assert_called_once()
                self.assertIn("sensitivity", error.call_args.args[0])

    def test_full_install_reuses_backend_and_preserves_existing_configs(self):
        for key, edition in EDITIONS.items():
            with self.subTest(edition=key), ExitStack() as stack:
                root = self.root / key
                configure(edition, root, "Old", 2)
                game_cfg = root / edition.config_dir / "game.cfg"
                game_cfg.write_text("// keep this custom file\n", encoding="utf-8")
                (root / "save.sav").write_bytes(b"save data")
                stages, outcomes, failures = [], [], []
                worker = InstallWorker(
                    edition, root, "account", {"name": "Ranger", "sensitivity": 3}
                )
                worker.stage.connect(
                    lambda number, title, stages=stages: stages.append(number)
                )
                worker.completed.connect(outcomes.append)
                worker.failed.connect(failures.append)
                stack.enter_context(
                    patch("quake_gui.worker.platform.system", return_value="Linux")
                )
                stack.enter_context(
                    patch("quake_gui.worker.platform.machine", return_value="x86_64")
                )
                stack.enter_context(patch.object(steamcmd, "check_rosetta"))
                stack.enter_context(
                    patch.object(
                        steamcmd,
                        "install_steamcmd",
                        return_value=self.root / "steamcmd",
                    )
                )
                stack.enter_context(patch.object(steamcmd, "update_steamcmd"))
                auth = stack.enter_context(patch.object(interactive, "login"))
                download = stack.enter_context(patch.object(gamedata, "download_quake"))
                data = stack.enter_context(
                    patch.object(
                        gamedata,
                        "install_single_player_game_data"
                        if key == "singleplayer"
                        else "install_multiplayer_game_data",
                    )
                )
                engine = stack.enter_context(
                    patch.object(
                        vkquake if key == "singleplayer" else ezquake,
                        "install_vkquake"
                        if key == "singleplayer"
                        else "install_ezquake",
                    )
                )
                stack.enter_context(
                    patch.object(
                        edition.launcher, "resolve_engine", return_value=root / "engine"
                    )
                )
                cleanup = stack.enter_context(patch.object(gamedata, "clean_up"))
                stack.enter_context(patch.object(Path, "home", return_value=self.root))
                worker.run()
                self.assertEqual(outcomes, ["success"])
                self.assertEqual(failures, [])
                self.assertEqual(stages, list(range(6)))
                auth.assert_called_once()
                download.assert_called_once_with(self.root / "steamcmd", "account", 3)
                data.assert_called_once_with(root)
                engine.assert_called_once_with(root)
                cleanup.assert_called_once_with(keep_steamcmd=True)
                shortcut = (
                    self.root
                    / ".local/share/applications"
                    / f"{edition.engine}.desktop"
                ).read_text(encoding="utf-8")
                self.assertIn(
                    "Exec=" + shortcuts.desktop_argument(root / "engine"), shortcut
                )
                self.assertIn('"+exec"', shortcut)
                self.assertIn("Terminal=false\n", shortcut)
                self.assertFalse((root / "launcher.py").exists())
                icon = f"{edition.engine}.svg"
                self.assertEqual(
                    (root / icon).read_bytes(),
                    (ASSETS / "icons" / icon).read_bytes(),
                )
                self.assertEqual(game_cfg.read_text(), "// keep this custom file\n")
                self.assertEqual((root / "save.sav").read_bytes(), b"save data")

    def test_backend_system_exit_is_reported_without_exiting_the_application(self):
        worker = InstallWorker(EDITIONS["singleplayer"], self.root, "account", {})
        outcomes, failures = [], []
        worker.completed.connect(outcomes.append)
        worker.failed.connect(failures.append)
        with patch.object(worker, "install", side_effect=SystemExit("download failed")):
            worker.run()
        self.assertEqual(outcomes, ["error"])
        self.assertEqual(failures, ["download failed"])

    def test_installation_failure_stops_later_steps_and_preserves_downloads(self):
        for edition in EDITIONS.values():
            campaign = edition.key == "singleplayer"
            steps = [
                "quake_gui.worker.steamcmd.check_rosetta",
                "quake_gui.worker.steamcmd.install_steamcmd",
                "quake_gui.worker.steamcmd.update_steamcmd",
                "quake_gui.worker.interactive.login",
                "quake_gui.worker.gamedata.download_quake",
                "quake_gui.worker.gamedata.install_single_player_game_data"
                if campaign
                else "quake_gui.worker.gamedata.install_multiplayer_game_data",
                "quake_gui.worker.vkquake.install_vkquake"
                if campaign
                else "quake_gui.worker.ezquake.install_ezquake",
                "quake_gui.worker.configure",
                "quake_gui.worker.create_shortcut",
                "quake_gui.worker.gamedata.clean_up",
            ]
            for failed_step in range(4, len(steps) - 1):
                with (
                    self.subTest(edition=edition.key, step=steps[failed_step]),
                    ExitStack() as stack,
                ):
                    mocks = [stack.enter_context(patch(step)) for step in steps]
                    mocks[failed_step].side_effect = RuntimeError("synthetic failure")
                    stack.enter_context(
                        patch.object(edition.launcher, "resolve_engine")
                    )
                    stack.enter_context(patch("quake_gui.worker.shutil.copyfile"))
                    worker = InstallWorker(
                        edition, self.root / edition.key, "account", {}
                    )
                    outcomes, failures = [], []
                    worker.completed.connect(outcomes.append)
                    worker.failed.connect(failures.append)
                    worker.run()
                    self.assertEqual(outcomes, ["error"])
                    self.assertEqual(failures, ["synthetic failure"])
                    for later_step in mocks[failed_step + 1 :]:
                        later_step.assert_not_called()

    def test_authentication_roundtrip_keeps_ui_responsive_and_clears_secret(self):
        window = self.window()
        window.username.setText("account")
        answers = []

        def install(worker):
            worker.next_stage(1, "Connecting")
            answers.append(worker.ask("Steam password"))

        with patch.object(InstallWorker, "install", install):
            window.start_install()
            self.pump_until(lambda: not window.auth.isHidden())
            self.assertFalse(window.install_button.isEnabled())
            window.secret.setText("secret-test-value")
            window.send_secret()
            self.pump_until(lambda: window.worker is None)
        self.assertEqual(answers, ["secret-test-value"])
        self.assertEqual(window.secret.text(), "")
        self.assertNotIn("secret-test-value", window.logs.toPlainText())
        self.assertNotIn("secret-test-value", (self.root / "settings.ini").read_text())
        self.assertTrue(window.install_button.isEnabled())

    def test_close_during_authentication_cancels_and_waits_for_worker(self):
        window = self.window()
        window.show()
        window.username.setText("account")

        def install(worker):
            worker.ask("Steam password")

        with patch.object(InstallWorker, "install", install):
            window.start_install()
            self.pump_until(lambda: not window.auth.isHidden())
            window.close()
            self.pump_until(lambda: window.worker is None)
        self.assertEqual(window.outcome, "cancelled")
        self.assertFalse(window.isVisible())

    def test_overlapping_destinations_are_rejected_before_installation(self):
        window = self.window()
        window.username.setText("account")
        window.directory.setText(str(self.root / "multiplayer" / "nested"))
        with patch.object(window, "error") as error:
            window.start_install()
        self.assertIsNone(window.worker)
        self.assertIn("separate folders", error.call_args.args[0])

    def test_play_starts_a_native_child_with_game_directory_and_reports_completion(
        self,
    ):
        window = self.window()
        root = self.root / "singleplayer"
        root.mkdir()
        output = root / "child.txt"
        # A real process exercises QProcess, working directory, configuration,
        # signals and button recovery without requiring a game or a GPU.
        command = [
            sys.executable,
            "-c",
            "from pathlib import Path; Path('child.txt').write_text('played')",
        ]
        with patch("quake_gui.window.launch_command", return_value=command):
            window.play()
            self.pump_until(lambda: window.game is None)
        self.assertEqual(output.read_text(), "played")
        self.assertTrue((root / "id1/configs/player.cfg").is_file())
        self.assertTrue(window.install_button.isEnabled())
        self.assertEqual(window.activity.text(), "Ready for another game")


if __name__ == "__main__":
    unittest.main()
