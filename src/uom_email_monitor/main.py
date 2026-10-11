"""Preserved email parsing, attachment forwarding, and UID delivery engine."""

import imaplib
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from .tls import create_tls_context

HOST = "mail.uom.lk"
PORT = 993
CHAT_ID = None
CHECK_INTERVAL = 30
RETRY_INTERVAL = 30
MAX_BODY_CHARS = 2900
STATE_FILE = Path.cwd() / "uom_monitor_state.json"
username = password = telegram_bot_token = None


def configure(config):
    global HOST, PORT, CHAT_ID, CHECK_INTERVAL, RETRY_INTERVAL, STATE_FILE
    global username, password, telegram_bot_token
    HOST, PORT, CHAT_ID = config.host, config.port, config.chat_id
    CHECK_INTERVAL, RETRY_INTERVAL = config.check_interval, config.retry_interval
    STATE_FILE = config.state_file
    username, password = config.username, config.password
    telegram_bot_token = config.telegram_bot_token

# Limits are decoded file bytes (not the encoded MIME size). Adjust as needed.
MAX_FILE_BYTES = 45 * 1024 * 1024          # 45 MiB / attachment
MAX_TOTAL_FILE_BYTES = 100 * 1024 * 1024   # 100 MiB / email
MAX_FILES_PER_EMAIL = 25                  # excess listed as skipped
MAX_PHOTO_BYTES = 10 * 1024 * 1024         # conservative photo limit
MAX_FILE_ATTEMPTS = 3                     # total, including across restarts

# Permitted forwarding types. Attachments are NEVER executed/opened locally.
# Archives are transported as files, NOT scanned for malicious content.
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".csv", ".tsv", ".txt", ".rtf", ".odt", ".ods", ".odp",
    ".zip", ".7z", ".rar", ".gz", ".tar", ".json", ".xml", ".ics",
    ".eml", ".epub", ".mp3", ".wav", ".mp4", ".mov",
}
BLOCKED_MIME_TYPES = {
    "image/svg+xml", "text/html", "application/xhtml+xml",
    "application/x-msdownload", "application/x-dosexec", "application/x-sh",
    "application/javascript", "text/javascript",
}


# ---------------------- IMAP and checkpoints ----------------------

def connect_mail():
    context = create_tls_context()
    mail = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=context, timeout=20)
    try:
        mail.login(username, password)
    except Exception:
        try:
            mail.logout()
        except Exception:
            pass
        raise
    print("Connected to UoM successfully!", flush=True)
    return mail


def load_state():
    if not STATE_FILE.exists():
        return None
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not {"uid_validity", "last_uid"} <= state.keys():
        raise ValueError("Invalid state file; inspect it instead of deleting it.")
    # Backward compatible with old {uid_validity, last_uid} state files.
    state["uid_validity"] = str(state["uid_validity"])
    state["last_uid"] = int(state["last_uid"])
    if not state["uid_validity"].isdigit() or state["last_uid"] < 0:
        raise ValueError("Invalid UID checkpoint; preserve and inspect the state file.")
    state.setdefault("pending", None)
    if state["pending"] is not None:
        p = state["pending"]
        if not isinstance(p, dict) or not {"uid", "text_sent", "files", "notice_sent"} <= p.keys():
            raise ValueError("Invalid pending delivery in state file.")
        p["uid"] = int(p["uid"])
        if (p["uid"] <= state["last_uid"] or not isinstance(p["text_sent"], bool)
                or not isinstance(p["notice_sent"], bool) or not isinstance(p["files"], dict)):
            raise ValueError("Invalid pending delivery in state file.")
        for info in p["files"].values():
            if (not isinstance(info, dict)
                    or info.get("status") not in {"sent", "skipped", "failed", "retry"}
                    or not isinstance(info.get("attempts"), int)
                    or info["attempts"] < 0 or not isinstance(info.get("reason"), str)):
                raise ValueError("Invalid attachment delivery checkpoint.")
    return state


def save_state(state):
    # Atomic replace, so an interruption does not normally leave half JSON.
    temporary = STATE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(STATE_FILE)
    if os.name != "nt":
        # Persist the directory entry as well as the JSON data after replacement.
        directory = os.open(STATE_FILE.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def get_mailbox_snapshot(mail):
    status, _ = mail.select("INBOX", readonly=True)
    if status != "OK":
        raise RuntimeError("Cannot select INBOX")
    _, values = mail.response("UIDVALIDITY")
    if not values or not values[0]:
        raise RuntimeError("Cannot obtain UIDVALIDITY")
    validity = values[0].decode("ascii") if isinstance(values[0], bytes) else str(values[0])
    status, data = mail.uid("SEARCH", None, "ALL")
    if status != "OK":
        raise RuntimeError("UID search failed")
    uids = sorted(int(uid) for uid in (data[0] or b"").split())
    return validity, uids


def fetch_message(mail, uid):
    # PEEK + readonly means no intentional modification of unread flags.
    status, response = mail.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if status != "OK":
        raise RuntimeError(f"Could not fetch UID {uid}")
    raw = next(
        (entry[1] for entry in response if isinstance(entry, tuple)
         and isinstance(entry[1], bytes)), None
    )
    if raw is None:
        raise RuntimeError(f"No email content for UID {uid}")
    return BytesParser(policy=policy.default).parsebytes(raw)


# ---------------------- Rich HTML rendering ----------------------

def is_safe_link(href):
    if not isinstance(href, str):
        return False
    href = href.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in href):
        return False
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def render_html_node(node, depth=0):
    """Convert untrusted email HTML into a limited safe Telegram HTML subset."""
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return escape(re.sub(r"\s+", " ", str(node)), quote=False)
    if not isinstance(node, Tag):
        return ""
    tag = node.name.lower()
    if tag in {"style", "script", "head", "svg", "iframe", "object", "form", "noscript"}:
        return ""
    if tag == "br":
        return "\n"
    if tag == "img":
        alt = str(node.get("alt") or "").strip()[:100]
        src = str(node.get("src") or "").strip()
        if src.lower().startswith("cid:"):
            return escape(f"[Embedded image{': ' + alt if alt else ''} — sent below]", quote=False)
        if src.lower().startswith(("http://", "https://")):
            return escape(f"[External image not downloaded{': ' + alt if alt else ''}]", quote=False)
        return escape(f"[Image: {alt}]", quote=False) if alt else ""
    if tag in {"ul", "ol"}:
        entries = []
        try:
            start = int(node.get("start", 1)) if tag == "ol" else 1
        except (TypeError, ValueError):
            start = 1
        for index, li in enumerate(node.find_all("li", recursive=False)):
            prefix = "  " * min(depth, 3) + ("• " if tag == "ul" else f"{start + index}. ")
            content = "".join(render_html_node(child, depth + 1) for child in li.children).strip()
            entries.append(prefix + content)
        return "\n" + "\n".join(entries) + "\n"
    if tag == "table":
        rows = []
        for tr in node.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            values = []
            for cell in cells:
                content = "".join(render_html_node(child, depth) for child in cell.children).strip()
                values.append(f"<b>{content}</b>" if cell.name == "th" else content)
            if values:
                rows.append(" | ".join(values))
        return "\n" + "\n".join(rows) + "\n" if rows else ""
    if tag == "pre":
        return "\n<pre>" + escape(node.get_text("", strip=False), quote=False) + "</pre>\n"
    content = "".join(render_html_node(child, depth) for child in node.children)
    if tag in {"b", "strong"}:
        return f"<b>{content}</b>"
    if tag in {"i", "em"}:
        return f"<i>{content}</i>"
    if tag in {"u", "ins"}:
        return f"<u>{content}</u>"
    if tag in {"s", "strike", "del"}:
        return f"<s>{content}</s>"
    if tag == "code":
        return f"<code>{escape(node.get_text('', strip=False), quote=False)}</code>"
    if tag == "a":
        href = node.get("href", "")
        if is_safe_link(href) and content.strip():
            return f'<a href="{escape(href.strip(), quote=True)}">{content}</a>'
        return content
    if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return f"\n\n<b>{content.strip()}</b>\n"
    if tag in {"p", "div", "section", "article", "blockquote", "tr"}:
        return "\n\n" + content.strip() + "\n\n"
    return content


class TelegramHTMLLimiter(HTMLParser):
    ALLOWED = {"b", "i", "u", "s", "a", "code", "pre"}

    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.length = 0
        self.parts = []
        self.stack = []
        self.truncated = False

    def handle_starttag(self, tag, attrs):
        if self.truncated or tag not in self.ALLOWED:
            return
        if tag == "a":
            href = dict(attrs).get("href", "")
            if not is_safe_link(href):
                return
            self.parts.append(f'<a href="{escape(href, quote=True)}">')
        else:
            self.parts.append(f"<{tag}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.truncated:
            return
        if self.stack and self.stack[-1] == tag:
            self.parts.append(f"</{tag}>")
            self.stack.pop()

    def handle_data(self, data):
        if self.truncated or not data:
            return
        remaining = self.limit - self.length
        if len(data) > remaining:
            self.parts.append(escape(data[:remaining], quote=False))
            self.length += remaining
            self.truncated = True
        else:
            self.parts.append(escape(data, quote=False))
            self.length += len(data)

    def finish(self):
        if self.truncated:
            self.parts.append("…")
        for tag in reversed(self.stack):
            self.parts.append(f"</{tag}>")
        return "".join(self.parts).strip()


def format_email_body(msg):
    html_part = msg.get_body(preferencelist=("html",))
    if html_part and html_part.get_content_type() == "text/html":
        soup = BeautifulSoup(html_part.get_content(), "html.parser")
        root = soup.body if soup.body is not None else soup
        rendered = "".join(render_html_node(child) for child in root.children)
        rendered = re.sub(r"[ \t]+\n", "\n", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        if rendered:
            limiter = TelegramHTMLLimiter(MAX_BODY_CHARS)
            limiter.feed(rendered)
            limiter.close()
            return limiter.finish()
    plain_part = msg.get_body(preferencelist=("plain",))
    if plain_part and plain_part.get_content_type() == "text/plain":
        body = plain_part.get_content().replace("\r\n", "\n").replace("\r", "\n")
        body = re.sub(r"[ \t]+", " ", body)
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        suffix = "…" if len(body) > MAX_BODY_CHARS else ""
        return escape(body[:MAX_BODY_CHARS], quote=False) + suffix
    return "(No readable text body)"


# ---------------------- MIME attachment discovery ----------------------

@dataclass
class MailFile:
    key: str
    filename: str
    mime: str
    role: str  # 'inline' or 'attachment'
    size: int
    content: bytes | None
    skip_reason: str | None = None


def safe_filename(value, fallback):
    # No local files are created, but sanitize names used in multipart metadata.
    name = str(value or fallback).replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip(" .")
    return (name or fallback)[:140]


def iter_candidate_parts(part):
    """Walk MIME leaves, retaining attached message/rfc822 as one .eml file."""
    if part.get_content_type() == "message/rfc822" and (
        part.get_filename() or part.get_content_disposition() == "attachment"
    ):
        yield part
    elif part.is_multipart():
        for child in part.iter_parts():
            yield from iter_candidate_parts(child)
    else:
        yield part


def cid_values(msg):
    """Find referenced Content-IDs from HTML variants; never fetch remote URLs."""
    found = set()
    for part in iter_candidate_parts(msg):
        if part.is_multipart() or part.get_content_type() != "text/html":
            continue
        if part.get_content_disposition() == "attachment":
            continue
        try:
            soup = BeautifulSoup(part.get_content(), "html.parser")
        except (UnicodeError, LookupError, ValueError):
            continue
        for image in soup.find_all("img"):
            src = str(image.get("src") or "").strip()
            if src.lower().startswith("cid:"):
                found.add(unquote(src[4:]).strip("<> ").lower())
    return found


def collect_files(msg):
    referenced_cids = cid_values(msg)
    found = []
    total_bytes = 0
    for part_index, part in enumerate(iter_candidate_parts(msg)):
        mime = part.get_content_type().lower()
        disp = part.get_content_disposition()
        original_name = part.get_filename()
        cid = str(part.get("Content-ID", "")).strip("<> ").lower()
        is_inline_image = mime.startswith("image/") and (
            disp == "inline" or (bool(cid) and cid in referenced_cids)
        )
        # Ignore alternative email body parts, but include explicitly attached
        # text files and embedded CID images.
        if not is_inline_image and not original_name and disp != "attachment":
            continue
        if mime in {"text/plain", "text/html"} and disp != "attachment" and not original_name:
            continue

        role = "inline" if is_inline_image else "attachment"
        extension = mimetypes.guess_extension(mime) or ".bin"
        fallback = f"embedded-image-{part_index}{extension}" if role == "inline" else f"attachment-{part_index}{extension}"
        filename = safe_filename(original_name, fallback)
        key = str(part_index)  # stable within the same MIME message on retry

        try:
            if mime == "message/rfc822":
                nested = part.get_payload()
                content = nested[0].as_bytes(policy=policy.default) if nested else b""
            else:
                content = part.get_payload(decode=True)
                if content is None:
                    raw = part.get_content()
                    content = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            size = len(content)
        except Exception:
            found.append(MailFile(key, filename, mime, role, 0, None, "Cannot decode MIME payload"))
            continue

        reason = None
        suffix = Path(filename).suffix.lower()
        if mime in BLOCKED_MIME_TYPES or suffix not in ALLOWED_EXTENSIONS:
            reason = "File type is not on the permitted forwarding list"
        elif len(found) >= MAX_FILES_PER_EMAIL:
            reason = f"More than {MAX_FILES_PER_EMAIL} files in this email"
        elif size > MAX_FILE_BYTES:
            reason = f"Exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB per-file limit"
        elif total_bytes + size > MAX_TOTAL_FILE_BYTES:
            reason = f"Exceeds {MAX_TOTAL_FILE_BYTES // (1024 * 1024)} MiB total email limit"
        elif not size:
            reason = "Empty or unreadable file"

        if reason:
            found.append(MailFile(key, filename, mime, role, size, None, reason))
        else:
            total_bytes += size
            found.append(MailFile(key, filename, mime, role, size, content))
    return found


# ---------------------- Telegram API ----------------------

class TelegramError(Exception):
    def __init__(self, reason, retryable=True, service_failure=False, retry_after=0, rate_limited=False):
        super().__init__(reason)
        self.retryable = retryable
        self.service_failure = service_failure
        self.retry_after = retry_after
        self.rate_limited = rate_limited


def telegram_request(method, *, data, files=None):
    """Raises sanitized errors, never logging a token-bearing endpoint."""
    url = f"https://api.telegram.org/bot{telegram_bot_token}/{method}"
    try:
        if files is None:
            response = requests.post(url, json=data, timeout=(10, 60))
        else:
            response = requests.post(url, data=data, files=files, timeout=(10, 90))
    except requests.RequestException as exc:
        raise TelegramError(f"Network error ({type(exc).__name__})", True) from None

    try:
        result = response.json()
    except ValueError:
        raise TelegramError(f"Non-JSON Telegram response (HTTP {response.status_code})", True)
    if response.ok and result.get("ok"):
        return result["result"].get("message_id")
    code = int(result.get("error_code", response.status_code))
    # 401/403 are global credential/access problems: do not discard an email.
    if code in {401, 403}:
        raise TelegramError(f"Telegram authorization/access failure (HTTP {code})", True, service_failure=True)
    if code == 429 or code >= 500:
        seconds = result.get("parameters", {}).get("retry_after", 0) if code == 429 else 0
        try:
            seconds = min(max(int(seconds), 0), 3600)
        except (TypeError, ValueError):
            seconds = 0
        raise TelegramError(f"Telegram temporary/rate-limit error (HTTP {code})", True, retry_after=seconds, rate_limited=(code == 429))
    raise TelegramError(f"Telegram rejected this item (HTTP {code})", False)


def send_text(sender, subject, body, file_count):
    message = (
        "📩 <b>New UoM Email</b>\n\n"
        f"👤 <b>From:</b> {escape(sender, quote=False)}\n"
        f"📌 <b>Subject:</b> {escape(subject, quote=False)}\n\n"
        f"📄 <b>Message:</b>\n{body}\n\n"
    )
    if file_count:
        message += f"📎 <i>{file_count} embedded image(s)/attachment(s) are being processed below.</i>\n\n"
    message += "🎓 <i>University of Moratuwa</i>"
    try:
        return telegram_request("sendMessage", data={
            "chat_id": CHAT_ID, "text": message, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
    except TelegramError as exc:
        if exc.retryable:
            raise
        # If Telegram rejects malformed/unsupported rich markup in one email,
        # retain the actual notification using a safe plain-text fallback.
        plain_body = BeautifulSoup(body, "html.parser").get_text(" ")[:3200]
        fallback = (
            "📩 New UoM Email\n\n"
            f"From: {sender}\nSubject: {subject}\n\n"
            f"Message: {plain_body}\n\n"
            f"Files detected: {file_count}\n"
            "(Rich formatting was not accepted by Telegram.)"
        )[:4000]
        return telegram_request("sendMessage", data={"chat_id": CHAT_ID, "text": fallback})


def send_file(item):
    # Telegram sendPhoto is for conventional PNG/JPEG; other images retain
    # their original file as sendDocument (GIF, WebP, TIFF, etc.).
    as_photo = (
        item.mime in {"image/png", "image/jpeg"}
        and item.size <= MAX_PHOTO_BYTES
    )
    method = "sendPhoto" if as_photo else "sendDocument"
    field = "photo" if as_photo else "document"
    label = "🖼 Embedded image" if item.role == "inline" else "📎 Attachment"
    caption = f"{label}: {item.filename}"[:900]
    try:
        return telegram_request(method, data={"chat_id": CHAT_ID, "caption": caption}, files={
            field: (item.filename, item.content, item.mime)
        })
    except TelegramError as exc:
        # Some images are valid files but cannot be decoded as Telegram photos.
        # A document upload preserves their original content instead.
        if not as_photo or exc.retryable:
            raise
        return telegram_request("sendDocument", data={"chat_id": CHAT_ID, "caption": caption}, files={
            "document": (item.filename, item.content, item.mime)
        })


def send_warning(subject, problems):
    # Truncate conservatively below the Telegram 4096-character limit.
    lines = ["⚠️ <b>UoM Attachment Delivery Warning</b>",
             f"<b>Subject:</b> {escape(subject[:180], quote=False)}",
             f"<b>{len(problems)} file(s) skipped or delivery unconfirmed.</b>", ""]
    for item, why in problems:
        line = f"• {escape(item.filename[:100], quote=False)}: {escape(why[:160], quote=False)}"
        if len("\n".join(lines)) + len(line) > 3600:
            lines.append("• Further skipped/failed items omitted from this summary; check the original email.")
            break
        lines.append(line)
    lines.extend(["", "Please open your UoM inbox for missing files."])
    return telegram_request("sendMessage", data={
        "chat_id": CHAT_ID, "text": "\n".join(lines), "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    })


def send_monitor_warning(text):
    return telegram_request("sendMessage", data={
        "chat_id": CHAT_ID, "text": "⚠️ UoM Email Monitor: " + text[:3000]
    })


# ---------------------- Delivery and checkpointing ----------------------

def process_email(mail, uid, state):
    msg = fetch_message(mail, uid)
    sender = str(msg.get("From", "Unknown"))[:150]
    subject = str(msg.get("Subject", "(No subject)"))[:200]
    files = collect_files(msg)

    if state["pending"] is None:
        state["pending"] = {
            "uid": uid,
            "text_sent": False,
            "files": {},
            "notice_sent": False,
        }
        save_state(state)
    pending = state["pending"]
    if int(pending["uid"]) != uid:
        raise RuntimeError("Pending UID does not match selected message")

    if not pending["text_sent"]:
        body = format_email_body(msg)
        send_text(sender, subject, body, len(files))
        pending["text_sent"] = True
        save_state(state)
        print(f"Email text sent, UID {uid}", flush=True)

    for item in files:
        info = pending["files"].get(item.key)
        if info and info["status"] in {"sent", "skipped", "failed"}:
            continue
        if item.skip_reason:
            pending["files"][item.key] = {
                "status": "skipped", "reason": item.skip_reason, "attempts": 0
            }
            save_state(state)
            print(f"Attachment skipped, UID {uid}, part {item.key}", flush=True)
            continue
        attempts = int(info.get("attempts", 0)) if info else 0
        try:
            send_file(item)
        except TelegramError as exc:
            if exc.service_failure or exc.rate_limited:
                # A broken token or denied chat affects ALL sends; hold the
                # whole queue rather than marking individual files as failed.
                raise
            attempts += 1
            if not exc.retryable or attempts >= MAX_FILE_ATTEMPTS:
                pending["files"][item.key] = {
                    "status": "failed", "reason": str(exc), "attempts": attempts
                }
                save_state(state)
                print(f"Attachment delivery failed, UID {uid}, part {item.key}; will report", flush=True)
                continue
            pending["files"][item.key] = {
                "status": "retry", "reason": str(exc), "attempts": attempts
            }
            save_state(state)
            raise TelegramError("File delivery pending; retry after reconnect", True,
                                retry_after=exc.retry_after) from None
        pending["files"][item.key] = {"status": "sent", "reason": "", "attempts": attempts + 1}
        save_state(state)
        print(f"Attachment sent, UID {uid}, part {item.key}", flush=True)

    problems = []
    for item in files:
        info = pending["files"].get(item.key)
        if info and info["status"] in {"failed", "skipped"}:
            problems.append((item, info["reason"]))
    if problems and not pending["notice_sent"]:
        send_warning(subject, problems)
        pending["notice_sent"] = True
        save_state(state)
        print(f"Delivery warning sent ({len(problems)} problem file(s))", flush=True)

    # Only move forward after ALL files were sent/skipped/failed and the
    # required warning was accepted by Telegram.
    if any(pending["files"].get(item.key, {}).get("status") not in {"sent", "skipped", "failed"}
           for item in files):
        raise RuntimeError("Some file status entries were not finalized")
    state["last_uid"] = uid
    state["pending"] = None
    save_state(state)
    print(f"Completed UID {uid}", flush=True)


def start_monitor():
    print("Starting UoM Email Monitor (rich text + file forwarding).", flush=True)
    print(f"Checking every {CHECK_INTERVAL} seconds. Press Ctrl+C to stop.\n", flush=True)
    state = load_state()
    while True:
        try:
            with connect_mail() as mail:
                while True:
                    validity, uids = get_mailbox_snapshot(mail)
                    if state is None:
                        last_uid = max(uids, default=0)
                        state = {"uid_validity": validity, "last_uid": last_uid, "pending": None}
                        save_state(state)
                        print("Baseline established; existing emails ignored. Last UID:", last_uid, flush=True)
                    elif state["uid_validity"] != validity:
                        send_monitor_warning(
                            "Mailbox UIDVALIDITY changed. The monitor must establish a new "
                            "baseline, so older messages in the newly identified mailbox will "
                            "not be replayed. Check the original inbox for anything missing, "
                            "especially attachments from an unfinished delivery."
                        )
                        last_uid = max(uids, default=0)
                        state = {"uid_validity": validity, "last_uid": last_uid, "pending": None}
                        save_state(state)
                        print("Mailbox UIDVALIDITY changed; new baseline created.", flush=True)

                    pending = state["pending"]
                    if pending and pending["uid"] not in uids:
                        send_monitor_warning(f"UID {pending['uid']} disappeared from INBOX before all delivery steps were completed. Check the original email for missing attachments.")
                        state["last_uid"] = max(state["last_uid"], pending["uid"])
                        state["pending"] = None
                        save_state(state)

                    new_uids = [uid for uid in uids if uid > state["last_uid"]]
                    if new_uids:
                        print(f"Found {len(new_uids)} new email(s).", flush=True)
                    for uid in new_uids:
                        process_email(mail, uid, state)

                    print("Monitoring inbox...", flush=True)
                    time.sleep(CHECK_INTERVAL)
        except KeyboardInterrupt:
            print("\nMonitor stopped by user.", flush=True)
            break
        except Exception as exc:
            # Avoid exposing bot tokens, email content, or credentials via URLs.
            delay = max(RETRY_INTERVAL, getattr(exc, "retry_after", 0))
            print(f"Monitor error ({type(exc).__name__}); retrying in {delay} seconds.", flush=True)
            time.sleep(delay)
