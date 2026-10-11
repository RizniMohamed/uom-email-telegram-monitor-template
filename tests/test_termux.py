"""Offline shell-manager tests using local Git and fake monitor/dependency commands."""

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from tests.offline import OfflineTestCase

SCRIPT = Path(__file__).resolve().parents[1] / "deployment" / "termux" / "uom.sh"
BASH = shutil.which("bash")
if BASH is None and os.name == "nt":
    candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    BASH = str(candidate) if candidate.is_file() else None


class TermuxTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        if not BASH or not shutil.which("git"):
            self.skipTest("Bash and Git are required for deployment tests")
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.home = Path(self.folder.name) / "home"
        self.home.mkdir()
        self.base = self.home / ".local/share/uom-monitor"
        self.base.mkdir(parents=True)
        self.repo = self.home / "repo"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Offline Test")
        self.git("config", "user.email", "offline@example.org")
        (self.repo / "dummy.txt").write_text("old version")
        self.git("add", ".")
        self.git("commit", "-m", "old")
        self.old = self.git("rev-parse", "HEAD").strip()
        # The local origin provides fetch semantics with no network.
        self.git("remote", "add", "origin", str(self.repo))
        (self.base / "repo.path").write_text(self.bash_path(self.repo) + "\n", newline="\n")
        self.state = json.dumps({"uid_validity": "123", "last_uid": 7,
                                 "pending": {"uid": 8, "text_sent": True, "notice_sent": False,
                                             "files": {"1": {"status": "sent", "attempts": 1,
                                                              "reason": ""}}}}).encode()
        self.secrets = b"PRIVATE-SENTINEL-DO-NOT-OVERWRITE\n"
        (self.base / "uom_monitor_state.json").write_bytes(self.state)
        (self.base / "monitor.env").write_bytes(self.secrets)
        self.fake_py = self.home / "fake-python"
        shebang = "/data/data/com.termux/files/usr/bin/bash" if Path("/data/data/com.termux/files/usr/bin/bash").exists() else "/usr/bin/env bash"
        self.fake_py.write_text(f"#!{shebang}\n" + '''case "$*" in
  '-m pip '*) echo dependencies >> "$UOM_TERMUX_HOME/events" ;;
  '-m compileall '*) ;;
  '-m unittest '*)
    echo tests >> "$UOM_TERMUX_HOME/events"
    [ ! -f "$UOM_TERMUX_HOME/FAIL_TEST" ] || exit 1 ;;
  '-m uom_email_monitor --check-config') exit 0 ;;
  '-m uom_email_monitor')
    echo started >> "$UOM_TERMUX_HOME/starts"
    echo 'Monitoring inbox...'
    trap 'exit 0' TERM INT
    while :; do sleep 1; done ;;
  *) exit 2 ;;
esac
''', encoding="utf-8", newline="\n")
        self.fake_py.chmod(0o700)
        self.env = {**os.environ, "HOME": self.bash_path(self.home),
                    "UOM_TERMUX_HOME": self.bash_path(self.base), "FAKE_PY": self.bash_path(self.fake_py),
                    "MSYS": "winsymlinks:sys",
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.shell('mkdir -p "$BASE/releases/$OLD/.venv/bin"; '
                   'cp "$FAKE_PY" "$BASE/releases/$OLD/.venv/bin/python"; '
                   'chmod 700 "$BASE/releases/$OLD/.venv/bin/python"; '
                   'touch "$BASE/releases/$OLD/.validated"; switch_release "$OLD"')

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True,
                              text=True, check=True, timeout=20).stdout

    def bash_path(self, path):
        if os.name != "nt":
            return str(path)
        return subprocess.run([BASH, "-c", 'cygpath -u "$1"', "_", str(path)],
                              capture_output=True, text=True, check=True, timeout=5).stdout.strip()

    def update_commit(self):
        (self.repo / "dummy.txt").write_text("new version")
        self.git("add", ".")
        self.git("commit", "-m", "new")
        return self.git("rev-parse", "HEAD").strip()

    def shell(self, code, *, mocks=False):
        prelude = '''source "$1"
init || exit 90
'''
        if mocks:
            prelude += '''python() {
  [[ "$1 $2" == '-m venv' ]] || return 1
  mkdir -p "$3/bin"
  cp "$FAKE_PY" "$3/bin/python"
  chmod 700 "$3/bin/python"
}
pid_alive() { [[ "$1" == "$BASE/manager.pid" ]]; }
terminate() { echo stop >> "$BASE/events"; }
start_monitor() { echo start >> "$BASE/events"; }
healthy() { [[ ! -f "$BASE/FAIL_HEALTH" ]]; }
'''
        result = subprocess.run([BASH, "-c", prelude + code, "_", SCRIPT.as_posix()],
                                env={**self.env, "OLD": self.old}, cwd=self.repo,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.base / "monitor.env").read_bytes(), self.secrets)
        self.assertEqual((self.base / "uom_monitor_state.json").read_bytes(), self.state)
        return result.stdout.strip()

    def events(self):
        path = self.base / "events"
        return path.read_text().splitlines() if path.exists() else []

    def test_unchanged_commit_never_restarts_or_installs(self):
        self.shell('check_updates; [[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertEqual(self.events(), [])

    def test_test_failure_keeps_working_release_and_secrets(self):
        new = self.update_commit()
        (self.base / "FAIL_TEST").touch()
        self.shell('! check_updates; [[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertEqual(self.events(), ["dependencies", "tests"])
        self.assertFalse((self.base / "releases" / new / ".validated").exists())
        self.assertEqual((self.base / "failed-commit").read_text().strip(), new)
        self.shell('check_updates', mocks=True)
        self.assertEqual(self.events(), ["dependencies", "tests"])

    def test_valid_update_switches_only_after_tests_and_keeps_state(self):
        new = self.update_commit()
        self.shell('check_updates', mocks=True)
        self.assertEqual(self.shell('current_sha'), new)
        self.assertEqual(self.events(), ["dependencies", "tests", "stop", "start"])
        self.assertEqual((self.base / "previous").read_text().strip(), self.old)
        self.assertFalse((self.base / "activation-pending").exists())

    def test_unhealthy_update_rolls_back_code_only(self):
        new = self.update_commit()
        (self.base / "FAIL_HEALTH").touch()
        self.shell('! check_updates; [[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertEqual(self.events(), ["dependencies", "tests", "stop", "start", "stop", "start"])
        self.assertIn(new, (self.base / "last-error").read_text())
        self.assertFalse((self.base / "activation-pending").exists())

    def test_interrupted_activation_recovers_previous_code_without_state_changes(self):
        new = self.update_commit()
        self.shell(f'prepare_release {new} && atomic_text "$BASE/activation-pending" "$OLD" && '
                   f'switch_release {new} && recover_interrupted && '
                   '[[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertFalse((self.base / "activation-pending").exists())

    def test_fetch_failure_keeps_monitor_and_retries_later(self):
        self.shell('timeout() { [[ "$*" != *fetch* ]] || return 1; command timeout "$@"; }; '
                   '! check_updates; [[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertEqual(self.events(), [])
        self.assertIn("retrying in five minutes", (self.base / "manager.log").read_text())

    def test_install_failure_does_not_activate(self):
        self.update_commit()
        self.shell('python() { return 1; }; ! check_updates; '
                   '[[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.base / "failed-commit").exists())

    def test_stopped_manager_only_validates_update(self):
        self.update_commit()
        self.shell('pid_alive() { return 1; }; check_updates; '
                   '[[ "$(current_sha)" == "$OLD" ]]', mocks=True)
        self.assertEqual(self.events(), ["dependencies", "tests"])

    def test_missing_migration_checkpoint_prevents_start(self):
        (self.base / "uom_monitor_state.json").unlink()
        # The harness normally checks preservation, so use a plain command here.
        result = subprocess.run([BASH, str(SCRIPT), "start"], env=self.env,
                                capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("migrate the existing checkpoint", result.stdout)
        self.assertFalse((self.base / "enabled").exists())
        (self.base / "uom_monitor_state.json").write_bytes(self.state)

    def test_log_rotation_keeps_writer_file_and_three_archives(self):
        path = self.base / "monitor.log"
        path.write_bytes(b"x" * (2 * 1024**2))
        self.shell('rotate_logs')
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual(path.with_name("monitor.log.1").stat().st_size, 2 * 1024**2)

    def test_duplicate_manager_lock_returns_without_starting_monitor(self):
        self.shell('flock() { return 1; }; run_manager', mocks=True)
        self.assertEqual(self.events(), [])

    def test_manager_recovers_crashed_monitor_during_fetch_outage(self):
        self.shell('''flock() { return 0; }
record_pid() { return 0; }
pid_alive() { [[ "$1" == "$BASE/manager.pid" || "$ALIVE" == 1 ]]; }
start_monitor() { echo start >> "$BASE/events"; ALIVE=1; }
timeout() { [[ "$*" != *fetch* ]] || return 1; command timeout "$@"; }
rotate_logs() { :; }
sleep() {
  LOOPS=$((LOOPS + 1)); ALIVE=0; SECONDS=$((SECONDS + 301))
  [[ "$LOOPS" != 2 ]] || rm "$BASE/enabled"
}
ALIVE=0; LOOPS=0; touch "$BASE/enabled"
run_manager
trap - EXIT TERM INT HUP
[[ "$(current_sha)" == "$OLD" ]]
''', mocks=True)
        self.assertEqual(self.events(), ["stop", "start", "start"])
        self.assertEqual((self.base / "manager.log").read_text().count("fetch failed"), 2)

    def test_real_supervisor_restarts_fake_process_and_retains_checkpoint(self):
        if os.name == "nt" or not shutil.which("flock") or not shutil.which("setsid"):
            self.skipTest("Real POSIX process/lock test runs on Linux/Termux in CI")
        binary = self.home / ".local/bin/uom"
        binary.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, binary)
        self.addCleanup(lambda: subprocess.run([BASH, str(binary), "stop"], env=self.env,
                                              capture_output=True, timeout=150))
        def command(operation):
            return subprocess.run([BASH, str(binary), operation], env=self.env,
                                  capture_output=True, text=True, check=True, timeout=150)
        command("start")
        deadline = time.monotonic() + 15
        while not (self.base / "starts").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue((self.base / "starts").exists())
        first = (self.base / "monitor.pid").read_text()
        self.assertIn("already running", command("start").stdout)
        self.assertEqual((self.base / "monitor.pid").read_text(), first)
        os.kill(int(first.split()[0]), signal.SIGKILL)
        deadline = time.monotonic() + 15
        while len((self.base / "starts").read_text().splitlines()) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(len((self.base / "starts").read_text().splitlines()), 2)
        self.assertNotEqual((self.base / "monitor.pid").read_text(), first)
        command("restart")
        deadline = time.monotonic() + 15
        while len((self.base / "starts").read_text().splitlines()) < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(len((self.base / "starts").read_text().splitlines()), 3)
        command("stop")
        self.assertIn("Manager stopped", command("status").stdout)
        self.assertEqual((self.base / "uom_monitor_state.json").read_bytes(), self.state)
