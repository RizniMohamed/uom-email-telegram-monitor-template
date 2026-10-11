"""Offline email-monitor tests; no UoM or Telegram API calls."""
import json
import tempfile
import unittest
from pathlib import Path
from email.message import EmailMessage
from unittest.mock import patch

from uom_email_monitor import main as m
from tests.offline import OfflineTestCase


def create_message():
    msg = EmailMessage()
    msg["From"] = "University <office@example.org>"
    msg["To"] = "Student <student@example.org>"
    msg["Subject"] = "Exam Notice"
    msg.set_content("Please see attached.")
    msg.add_alternative('<html><body><p><b>Important</b> notice: <a href="https://uom.lk">website</a></p><p><img src="cid:exam-image" alt="notice"></p><p><img src="https://example.org/track.png" alt="remote"></p><ol><li>First</li><li>Second</li></ol></body></html>', subtype="html")
    html_part = msg.get_payload()[1]
    html_part.add_related(b"fake-png", maintype="image", subtype="png", cid="<exam-image>", filename="notice.png", disposition="inline")
    msg.add_attachment(b"%PDF-1.4\nexample", maintype="application", subtype="pdf", filename="timetable.pdf")
    msg.add_attachment(b"MZfake", maintype="application", subtype="octet-stream", filename="unsafe.exe")
    return msg


class MonitorTests(OfflineTestCase):
    def test_html_and_inline_images(self):
        msg = create_message()
        html = m.format_email_body(msg)
        self.assertIn('<b>Important</b>', html)
        self.assertIn('href="https://uom.lk"', html)
        self.assertIn('Embedded image', html)
        self.assertIn('External image not downloaded', html)
        self.assertIn('1. First', html)
        files = m.collect_files(msg)
        self.assertEqual(len(files), 3)
        self.assertEqual(files[0].role, "inline")
        self.assertEqual(files[0].filename, "notice.png")
        self.assertEqual(files[1].filename, "timetable.pdf")
        self.assertIsNone(files[1].skip_reason)
        self.assertEqual(files[2].filename, "unsafe.exe")
        self.assertIsNotNone(files[2].skip_reason)

    def test_size_cap_and_warning(self):
        msg = create_message()
        with patch.object(m, "MAX_FILE_BYTES", 10):
            files = m.collect_files(msg)
        self.assertIn("limit", files[1].skip_reason)

    def test_old_state_compatible(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, "STATE_FILE", Path(d) / "uom_monitor_state.json"):
                m.STATE_FILE.write_text(json.dumps({"uid_validity": "123", "last_uid": 7}))
                state = m.load_state()
                self.assertEqual(state["last_uid"], 7)
                self.assertIsNone(state["pending"])
                m.save_state(state)
                self.assertIsNone(m.load_state()["pending"])

    def test_per_file_resume_and_warning(self):
        msg = create_message()
        class FakeMail:
            pass
        calls = {"text": 0, "files": [], "warnings": []}
        fail_once = {"timetable.pdf": True}
        def fake_text(*args):
            calls["text"] += 1
            return 101
        def fake_file(item):
            calls["files"].append(item.filename)
            if item.filename == "timetable.pdf" and fail_once[item.filename]:
                fail_once[item.filename] = False
                raise m.TelegramError("Network error (Timeout)", True)
            return 102
        def fake_warn(subject, problems):
            calls["warnings"].append([(f.filename, reason) for f, reason in problems])
            return 103
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, "STATE_FILE", Path(d) / "state.json"), \
                 patch.object(m, "fetch_message", return_value=msg), \
                 patch.object(m, "send_text", side_effect=fake_text), \
                 patch.object(m, "send_file", side_effect=fake_file), \
                 patch.object(m, "send_warning", side_effect=fake_warn):
                state = {"uid_validity": "9", "last_uid": 10, "pending": None}
                with self.assertRaises(m.TelegramError):
                    m.process_email(FakeMail(), 11, state)
                self.assertTrue(state["pending"]["text_sent"])
                # Model a process restart by loading from the checkpoint.
                state = m.load_state()
                m.process_email(FakeMail(), 11, state)
                self.assertEqual(calls["text"], 1)
                self.assertEqual(calls["files"].count("notice.png"), 1)
                self.assertEqual(calls["files"].count("timetable.pdf"), 2)
                self.assertEqual(len(calls["warnings"]), 1)
                self.assertEqual(calls["warnings"][0][0][0], "unsafe.exe")
                self.assertEqual(state["last_uid"], 11)
                self.assertIsNone(state["pending"])

    def test_photo_rejected_falls_back_to_original_document(self):
        item = m.MailFile("1", "notice.png", "image/png", "inline", 8, b"not a photo")
        methods = []
        def fake_request(method, **kwargs):
            methods.append(method)
            if method == "sendPhoto":
                raise m.TelegramError("Telegram rejected this item (HTTP 400)", False)
            return 77
        with patch.object(m, "telegram_request", side_effect=fake_request):
            result = m.send_file(item)
        self.assertEqual(result, 77)
        self.assertEqual(methods, ["sendPhoto", "sendDocument"])

    def test_attached_eml_is_a_single_file(self):
        outer = EmailMessage()
        outer.set_content("See forwarded email")
        nested = EmailMessage()
        nested["From"] = "a@example.org"
        nested["Subject"] = "Forwarded"
        nested.set_content("Nested body")
        outer.add_attachment(nested, filename="previous.eml")
        files = m.collect_files(outer)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "previous.eml")
        self.assertIn(b"Nested body", files[0].content)

    def test_last_attempt_failure_is_reported(self):
        msg = create_message()
        attempts = []
        reports = []
        def fail(item):
            attempts.append(item.filename)
            raise m.TelegramError("Network error (Timeout)", True)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(m, "STATE_FILE", Path(d) / "state.json"), \
                 patch.object(m, "fetch_message", return_value=msg), \
                 patch.object(m, "send_text", return_value=1), \
                 patch.object(m, "send_file", side_effect=fail), \
                 patch.object(m, "send_warning", side_effect=lambda sub, probs: reports.extend(probs)):
                state = {"uid_validity": "1", "last_uid": 1, "pending": None}
                for i in range(10):
                    try:
                        m.process_email(None, 2, state)
                        break
                    except m.TelegramError:
                        state = m.load_state()
                self.assertEqual(state["last_uid"], 2)
                self.assertTrue(any(f.filename == "notice.png" for f, reason in reports))
                self.assertTrue(any(f.filename == "timetable.pdf" for f, reason in reports))
                self.assertEqual(attempts.count("notice.png"), 3)
                self.assertEqual(attempts.count("timetable.pdf"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
