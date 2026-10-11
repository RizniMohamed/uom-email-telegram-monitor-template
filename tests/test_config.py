import tempfile
from pathlib import Path
from unittest.mock import patch

from uom_email_monitor import cli, main as monitor
from uom_email_monitor.config import ConfigurationError, load_config
from tests.offline import OfflineTestCase

DUMMY = {"UOM_USERNAME": "dummy@example.org", "UOM_PASSWORD": "dummy-secret",
         "TELEGRAM_BOT_TOKEN": "123:dummy_token", "TELEGRAM_CHAT_ID": "123"}


class ConfigurationTests(OfflineTestCase):
    def test_defaults_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('UOM_PASSWORD="file-secret"\nCHECK_INTERVAL=45\n'
                            'UOM_STATE_FILE=state.json\n', encoding="utf-8")
            config = load_config(path, DUMMY)
            self.assertEqual(config.password, "dummy-secret")
            self.assertEqual(config.check_interval, 45)
            self.assertEqual(config.retry_interval, 30)
            self.assertEqual(config.state_file, Path(directory) / "state.json")
            for secret in DUMMY.values():
                self.assertNotIn(secret, repr(config))

    def test_credentials_are_not_interpolated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('UOM_PASSWORD="literal-${HOME}-value"\n', encoding="utf-8")
            env = {k: v for k, v in DUMMY.items() if k != "UOM_PASSWORD"}
            self.assertEqual(load_config(path, env).password, "literal-${HOME}-value")

    def test_validation_never_echoes_values(self):
        bad_values = {"UOM_PASSWORD": "", "TELEGRAM_BOT_TOKEN": "sensitive-invalid-token",
                      "TELEGRAM_CHAT_ID": "sensitive-chat", "CHECK_INTERVAL": "-2",
                      "UOM_IMAP_PORT": "65536", "UOM_IMAP_HOST": "bad\nhost"}
        with tempfile.TemporaryDirectory() as directory:
            for key, value in bad_values.items():
                with self.subTest(key=key), self.assertRaises(ConfigurationError) as caught:
                    load_config(Path(directory) / "absent.env", {**DUMMY, key: value})
                if value:
                    self.assertNotIn(value, str(caught.exception))
            with self.assertRaises(ConfigurationError):
                load_config(Path(directory) / "absent.env", {})

    def test_cli_check_preserves_state_and_never_starts_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            content = b'{"uid_validity":"123","last_uid":7,"pending":null}'
            path.write_bytes(content)
            config = load_config(Path(directory) / ".env", {**DUMMY, "UOM_STATE_FILE": str(path)})
            with patch.object(cli, "load_config", return_value=config), \
                 patch.object(monitor, "start_monitor") as start, \
                 patch("builtins.print"):
                self.assertEqual(cli.main(["--check-config"]), 0)
            start.assert_not_called()
            self.assertEqual(path.read_bytes(), content)

    def test_mocked_startup_uses_validated_config_and_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            config = load_config(Path(directory) / ".env", {**DUMMY, "UOM_STATE_FILE": str(path)})
            with patch.object(cli, "load_config", return_value=config), \
                 patch.object(monitor, "start_monitor") as start, \
                 patch.object(cli.signal, "signal"):
                self.assertEqual(cli.main([]), 0)
            start.assert_called_once()
            self.assertEqual(monitor.STATE_FILE, path)
            self.assertFalse(path.exists())

    def test_tls_probe_does_not_load_credentials(self):
        with patch.object(cli, "probe_imap_tls", return_value=("TLSv1.2", "dummy-cipher")), \
             patch.object(cli, "load_config") as config, patch("builtins.print"):
            self.assertEqual(cli.main(["--check-imap-tls"]), 0)
        config.assert_not_called()
