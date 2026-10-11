from unittest.mock import MagicMock, patch

import requests

from uom_email_monitor import main as m
from tests.offline import OfflineTestCase


class TelegramTests(OfflineTestCase):
    def test_api_errors_are_sanitized_and_classified(self):
        for code in (400, 401, 403, 429, 500):
            response = MagicMock(status_code=code, ok=False)
            response.json.return_value = {"ok": False, "error_code": code,
                                          "description": "private-server-detail",
                                          "parameters": {"retry_after": 61}}
            with self.subTest(code=code), patch.object(m.requests, "post", return_value=response), \
                 self.assertRaises(m.TelegramError) as caught:
                m.telegram_request("sendMessage", data={"chat_id": "123"})
            self.assertNotIn("private-server-detail", str(caught.exception))
            self.assertEqual(caught.exception.retryable, code != 400)
            self.assertEqual(caught.exception.service_failure, code in {401, 403})
            self.assertEqual(caught.exception.rate_limited, code == 429)
            if code == 429:
                self.assertEqual(caught.exception.retry_after, 61)

    def test_network_and_non_json_errors(self):
        with patch.object(m.requests, "post", side_effect=requests.Timeout("token-bearing-url")), \
             self.assertRaises(m.TelegramError) as caught:
            m.telegram_request("sendMessage", data={})
        self.assertNotIn("token-bearing-url", str(caught.exception))
        response = MagicMock(status_code=502)
        response.json.side_effect = ValueError()
        with patch.object(m.requests, "post", return_value=response), self.assertRaises(m.TelegramError):
            m.telegram_request("sendMessage", data={})

    def test_success_and_multipart_timeout(self):
        response = MagicMock(ok=True)
        response.json.return_value = {"ok": True, "result": {"message_id": 9}}
        with patch.object(m.requests, "post", return_value=response) as post:
            self.assertEqual(m.telegram_request("sendDocument", data={}, files={"document": b"dummy"}), 9)
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 90))

    def test_text_rejection_uses_plain_fallback(self):
        with patch.object(m, "telegram_request", side_effect=[m.TelegramError("rejected", False), 9]) as request:
            self.assertEqual(m.send_text("dummy", "subject", "<b>body</b>", 0), 9)
        data = request.call_args.kwargs["data"]
        self.assertNotIn("parse_mode", data)
        self.assertIn("Message: body", data["text"])

    def test_warning_escapes_names_and_truncates(self):
        item = m.MailFile("1", "<unsafe>.exe", "application/octet-stream", "attachment", 1, None)
        with patch.object(m, "telegram_request", return_value=1) as request:
            m.send_warning("<subject>", [(item, "<blocked>" + "x" * 300)] * 100)
        text = request.call_args.kwargs["data"]["text"]
        self.assertIn("&lt;unsafe&gt;.exe", text)
        self.assertIn("&lt;subject&gt;", text)
        self.assertIn("Further skipped", text)
        self.assertLess(len(text), 4096)

    def test_large_photo_is_document(self):
        item = m.MailFile("1", "image.png", "image/png", "inline", m.MAX_PHOTO_BYTES + 1, b"dummy")
        with patch.object(m, "telegram_request", return_value=1) as request:
            m.send_file(item)
        self.assertEqual(request.call_args.args[0], "sendDocument")
