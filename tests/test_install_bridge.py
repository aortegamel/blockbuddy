import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "tools" / "install_bridge.py"


class InstallBridgeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        spec = importlib.util.spec_from_file_location("install_bridge_under_test", INSTALLER)
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.instance = self.root / "Minecraft instance"
        self.instance.mkdir()
        self.source = self.root / "agent_bridge.py"
        self.source.write_bytes(b"# bridge source\n")
        self.minescript = self.instance / "minescript"
        self.config = self.minescript / "config.txt"
        self.destination = self.minescript / "agent_bridge.py"

    def install(self, python="/chosen/python"):
        with (
            patch.dict(os.environ, {"BLOCKBUDDY_MC_DIR": str(self.instance)}),
            patch.object(self.installer, "SRC", self.source),
            patch.object(self.installer.shutil, "which", return_value=python),
            patch.object(self.installer.sys, "executable", "/fallback/python"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.installer.main()

    def test_import_without_environment_uses_the_documented_source(self):
        self.assertNotIn("BLOCKBUDDY_MC_DIR", os.environ)
        self.assertEqual(
            self.installer.SRC, ROOT / "bridge" / "minescript" / "agent_bridge.py"
        )
        self.assertTrue(callable(self.installer.main))

    def test_main_reports_missing_or_empty_environment_before_file_changes(self):
        for environment in ({}, {"BLOCKBUDDY_MC_DIR": ""}):
            with self.subTest(environment=environment):
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch.object(self.installer.shutil, "copy2") as copy,
                    patch.object(Path, "mkdir") as mkdir,
                    self.assertRaisesRegex(SystemExit, "BLOCKBUDDY_MC_DIR"),
                ):
                    self.installer.main()
                copy.assert_not_called()
                mkdir.assert_not_called()
        self.assertFalse(self.minescript.exists())

    def test_initial_install_copies_bridge_and_creates_config(self):
        self.install()
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(
            self.config.read_text(encoding="utf-8"),
            'python="/chosen/python"\nautorun[*]=agent_bridge\n',
        )

    def test_new_config_uses_current_python_when_path_lookup_fails(self):
        self.install(python=None)
        self.assertEqual(
            self.config.read_text(encoding="utf-8"),
            'python="/fallback/python"\nautorun[*]=agent_bridge\n',
        )

    def test_refresh_replaces_bridge_without_changing_config(self):
        self.install()
        original_config = self.config.read_bytes()
        self.source.write_bytes(b"# refreshed bridge\n")
        self.install()
        self.install()
        self.assertEqual(self.destination.read_bytes(), b"# refreshed bridge\n")
        self.assertEqual(self.config.read_bytes(), original_config)

    def test_append_preserves_player_settings_and_all_existing_bytes(self):
        configurations = (
            b"",
            b'python="/player/python"\ncommand_path=custom\n',
            b'python="/player/python"\ncommand_path=custom',
            b'python="/player/python"\r\n# player comment\r\n\r\n',
            b'python="/player/python"\n\n\n',
        )
        self.minescript.mkdir()
        for original in configurations:
            with self.subTest(original=original):
                self.config.write_bytes(original)
                self.install()
                separator = b"" if not original or original.endswith(b"\n") else b"\n"
                expected = original + separator + b"autorun[*]=agent_bridge\n"
                self.assertEqual(self.config.read_bytes(), expected)
                self.install()
                self.assertEqual(self.config.read_bytes(), expected)

    def test_existing_active_autoruns_remain_unchanged(self):
        entries = (
            "autorun[*]=player_script",
            "  \tautorun[*] = player_script\n",
            "autorun[*]=\n",
            "autorun[*]=agent_bridge\n",
            "autorun[*]=first_script\nautorun[*]=second_script\n",
            "# autorun[*]=agent_bridge\nautorun[*]=player_script\n",
        )
        self.minescript.mkdir()
        for entry in entries:
            with self.subTest(entry=entry):
                original = ('python="/player/python"\n' + entry).encode("utf-8")
                self.config.write_bytes(original)
                self.install()
                self.assertEqual(self.config.read_bytes(), original)
                self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())

    def test_comments_and_other_settings_do_not_count_as_active_autoruns(self):
        entries = (
            "# autorun[*]=disabled_script\n",
            "  #autorun[*] = disabled_script\n",
            "# Example: autorun[*]=agent_bridge\n",
            "message=autorun[*]\n",
            "autorun[world]=world_script\n",
        )
        self.minescript.mkdir()
        for entry in entries:
            with self.subTest(entry=entry):
                original = ('python="/player/python"\n' + entry).encode("utf-8")
                self.config.write_bytes(original)
                self.install()
                expected = original + b"autorun[*]=agent_bridge\n"
                self.assertEqual(self.config.read_bytes(), expected)
                self.install()
                self.assertEqual(self.config.read_bytes(), expected)


if __name__ == "__main__":
    unittest.main()
