import imaplib
import ssl
import tempfile
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from uom_email_monitor import main as m
from uom_email_monitor.lock import AlreadyRunningError, monitor_lock
from uom_email_monitor.tls import CIPHERS, create_tls_context, probe_imap_tls
from tests.offline import OfflineTestCase
from tests.test_monitor import create_message


class ReliabilityTests(OfflineTestCase):
    def test_html_styles_lists_and_sanitized_links(self):
        msg = EmailMessage()
        msg.set_content("plain")
        msg.add_alternative('<p><b>bold</b> <em>italic</em> <u>underline</u></p>'
                            '<ul><li>bullet</li></ul><ol start="3"><li>third</li></ol>'
                            '<script>hidden</script><a href="javascript:bad()">unsafe</a>'
                            '<a href="https://[invalid">malformed</a>'
                            '<a href="https://example.org/?a=1&amp;b=2">safe</a>', subtype="html")
        body = m.format_email_body(msg)
        for expected in ("<b>bold</b>", "<i>italic</i>", "<u>underline</u>",
                         "• bullet", "3. third", "unsafe", "malformed", "a=1&amp;b=2"):
            self.assertIn(expected, body)
        self.assertNotIn("javascript", body)
        self.assertNotIn("hidden", body)
        self.assertFalse(m.is_safe_link("https://example.org/\nsecret"))

    def test_plain_text_fallback_and_truncation(self):
        msg = EmailMessage()
        msg.set_content("<plain> & " + "x" * 3000)
        msg.add_alternative("<script>hidden</script>", subtype="html")
        body = m.format_email_body(msg)
        self.assertTrue(body.startswith("&lt;plain&gt; &amp;"))
        self.assertTrue(body.endswith("…"))

    def test_html_truncation_closes_tags(self):
        msg = EmailMessage()
        msg.set_content("fallback")
        msg.add_alternative("<b><i>" + "x" * 3000 + "</i></b>", subtype="html")
        body = m.format_email_body(msg)
        self.assertTrue(body.endswith("…</i></b>"))

    def test_limits_remain_unchanged(self):
        self.assertEqual((m.MAX_FILE_BYTES, m.MAX_TOTAL_FILE_BYTES, m.MAX_PHOTO_BYTES,
                          m.MAX_FILES_PER_EMAIL, m.MAX_FILE_ATTEMPTS),
                         (45 * 1024**2, 100 * 1024**2, 10 * 1024**2, 25, 3))

    def test_attachment_total_count_and_exact_limit(self):
        msg = EmailMessage()
        msg.set_content("body")
        for i in range(3):
            msg.add_attachment(b"1234", maintype="application", subtype="pdf", filename=f"{i}.pdf")
        with patch.object(m, "MAX_FILE_BYTES", 4), patch.object(m, "MAX_TOTAL_FILE_BYTES", 8):
            files = m.collect_files(msg)
        self.assertIsNone(files[0].skip_reason)
        self.assertIsNone(files[1].skip_reason)
        self.assertIn("total", files[2].skip_reason)
        with patch.object(m, "MAX_FILES_PER_EMAIL", 1):
            self.assertIn("More than", m.collect_files(msg)[1].skip_reason)

    def test_tls_policy_and_probe_never_login(self):
        context = create_tls_context()
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertEqual(CIPHERS, "ECDHE+AESGCM:ECDHE+CHACHA20:AES256-GCM-SHA384:AES128-GCM-SHA256")
        fake = MagicMock()
        fake.sock.version.return_value = "TLSv1.2"
        fake.sock.cipher.return_value = ("AES256-GCM-SHA384", "TLSv1.2", 256)
        with patch("uom_email_monitor.tls.imaplib.IMAP4_SSL", return_value=fake) as connect:
            self.assertEqual(probe_imap_tls()[0], "TLSv1.2")
        probe_context = connect.call_args.kwargs["ssl_context"]
        self.assertEqual(probe_context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(probe_context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(probe_context.check_hostname)
        fake.login.assert_not_called()
        fake.select.assert_not_called()
        fake.logout.assert_not_called()
        fake.shutdown.assert_called_once_with()

    def test_tls_probe_closes_transport_without_unauthenticated_logout(self):
        fake = MagicMock()
        fake._file = fake.file  # Python 3.14 renamed the transport file.
        fake.state = "NONAUTH"
        fake.__enter__.return_value = fake
        fake.__exit__.side_effect = lambda *args: imaplib.IMAP4.__exit__(fake, *args)
        fake.logout.side_effect = imaplib.IMAP4.abort("command: LOGOUT => socket error: EOF")
        fake.shutdown.side_effect = lambda: imaplib.IMAP4.shutdown(fake)
        fake.sock.version.return_value = "TLSv1.2"
        fake.sock.cipher.return_value = ("AES256-GCM-SHA384", "TLSv1.2", 256)
        with patch("uom_email_monitor.tls.imaplib.IMAP4_SSL", return_value=fake):
            self.assertEqual(probe_imap_tls(), ("TLSv1.2", "AES256-GCM-SHA384"))
        fake.logout.assert_not_called()
        fake.shutdown.assert_called_once_with()
        fake.file.close.assert_called_once_with()
        fake.sock.close.assert_called_once_with()

    def test_readonly_uid_fetch_keeps_email_unread(self):
        mail = MagicMock()
        mail.select.return_value = ("OK", [])
        mail.response.return_value = ("UIDVALIDITY", [b"123"])
        mail.uid.side_effect = [("OK", [b"9 2"]), ("OK", [(b"meta", b"Subject: Dummy\r\n\r\nBody")])]
        self.assertEqual(m.get_mailbox_snapshot(mail), ("123", [2, 9]))
        self.assertEqual(m.fetch_message(mail, 9)["Subject"], "Dummy")
        mail.select.assert_called_once_with("INBOX", readonly=True)
        self.assertEqual(mail.uid.call_args.args, ("FETCH", "9", "(BODY.PEEK[])"))

    def test_corrupt_state_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE_FILE", Path(directory) / "state.json"):
            for content in ('{broken', '{"uid_validity":"1","last_uid":-1}',
                            '{"uid_validity":"1","last_uid":1,"pending":{"uid":2,"text_sent":false,"notice_sent":false,"files":[]}}'):
                m.STATE_FILE.write_text(content)
                with self.assertRaises(ValueError):
                    m.load_state()
                self.assertEqual(m.STATE_FILE.read_text(), content)

    def test_atomic_save_failure_preserves_previous_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE_FILE", Path(directory) / "state.json"):
            old = {"uid_validity": "1", "last_uid": 5, "pending": None}
            m.save_state(old)
            with patch.object(Path, "replace", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    m.save_state({**old, "last_uid": 6})
            self.assertEqual(m.load_state(), old)

    def test_duplicate_process_lock_and_release(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with monitor_lock(path):
                with self.assertRaises(AlreadyRunningError):
                    with monitor_lock(path):
                        self.fail("Second process acquired lock")
            with monitor_lock(path):
                self.assertFalse(path.exists())

    def run_cycle(self, state, validity="1", uids=None):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE_FILE", Path(directory) / "state.json"):
            if state is not None:
                m.save_state(state)
            mail = MagicMock()
            mail.__enter__.return_value = mail
            with patch.object(m, "connect_mail", return_value=mail), \
                 patch.object(m, "get_mailbox_snapshot", return_value=(validity, uids or [])), \
                 patch.object(m, "process_email") as process, \
                 patch.object(m, "send_monitor_warning", return_value=1) as warning, \
                 patch.object(m.time, "sleep", side_effect=KeyboardInterrupt):
                m.start_monitor()
            return m.load_state(), process, warning

    def test_initial_baseline_and_duplicate_detection_after_restart(self):
        state, process, _ = self.run_cycle(None, uids=[1, 5])
        self.assertEqual(state["last_uid"], 5)
        process.assert_not_called()
        _, process, _ = self.run_cycle(state, uids=[1, 5, 6])
        self.assertEqual([c.args[1] for c in process.call_args_list], [6])

    def test_uidvalidity_change_warns_and_baselines(self):
        state, process, warning = self.run_cycle({"uid_validity": "1", "last_uid": 5, "pending": None},
                                                validity="2", uids=[1, 7])
        self.assertEqual((state["uid_validity"], state["last_uid"]), ("2", 7))
        warning.assert_called_once()
        process.assert_not_called()

    def test_disappeared_pending_uid_warns(self):
        old = {"uid_validity": "1", "last_uid": 5,
               "pending": {"uid": 6, "text_sent": True, "files": {}, "notice_sent": False}}
        state, _, warning = self.run_cycle(old, uids=[5])
        self.assertEqual(state["last_uid"], 6)
        self.assertIsNone(state["pending"])
        warning.assert_called_once()

    def test_connection_failure_reconnects(self):
        mail = MagicMock()
        with patch.object(m, "load_state", return_value={"uid_validity": "1", "last_uid": 0, "pending": None}), \
             patch.object(m, "connect_mail", side_effect=[OSError(), mail]) as connect, \
             patch.object(m, "get_mailbox_snapshot", return_value=("1", [])), \
             patch.object(m.time, "sleep", side_effect=[None, KeyboardInterrupt]):
            m.start_monitor()
        self.assertEqual(connect.call_count, 2)

    def test_warning_failure_does_not_repeat_accepted_files(self):
        msg = create_message()
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(m, "fetch_message", return_value=msg), patch.object(m, "send_text", return_value=1) as text, \
             patch.object(m, "send_file", return_value=2) as send, \
             patch.object(m, "send_warning", side_effect=[m.TelegramError("temporary"), 3]):
            state = {"uid_validity": "1", "last_uid": 1, "pending": None}
            with self.assertRaises(m.TelegramError):
                m.process_email(None, 2, state)
            self.assertEqual(m.load_state()["last_uid"], 1)
            m.process_email(None, 2, m.load_state())
            text.assert_called_once()
            self.assertEqual(send.call_count, 2)
            self.assertEqual(m.load_state()["last_uid"], 2)

    def test_rate_limit_does_not_consume_file_attempts(self):
        msg = create_message()
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(m, "fetch_message", return_value=msg), patch.object(m, "send_text", return_value=1), \
             patch.object(m, "send_file", side_effect=m.TelegramError("rate limit", retry_after=60, rate_limited=True)):
            state = {"uid_validity": "1", "last_uid": 1, "pending": None}
            with self.assertRaises(m.TelegramError) as caught:
                m.process_email(None, 2, state)
            self.assertEqual(caught.exception.retry_after, 60)
            self.assertEqual(m.load_state()["pending"]["files"], {})

    def test_authorization_failure_holds_queue(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(m, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(m, "fetch_message", return_value=create_message()), patch.object(m, "send_text", return_value=1), \
             patch.object(m, "send_file", side_effect=m.TelegramError("auth", service_failure=True)):
            state = {"uid_validity": "1", "last_uid": 1, "pending": None}
            with self.assertRaises(m.TelegramError):
                m.process_email(None, 2, state)
            self.assertEqual(m.load_state()["last_uid"], 1)
            self.assertEqual(m.load_state()["pending"]["files"], {})
