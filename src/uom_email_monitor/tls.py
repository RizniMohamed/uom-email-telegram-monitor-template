"""The existing tested UoM TLS policy, shared with the credential-free probe."""

import imaplib
import ssl

CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20:AES256-GCM-SHA384:AES128-GCM-SHA256"


def create_tls_context():
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(CIPHERS)
    return context


def probe_imap_tls(host="mail.uom.lk", port=993):
    # IMAP greeting and TLS handshake only: no login or mailbox selection.
    mail = imaplib.IMAP4_SSL(host, port, ssl_context=create_tls_context(), timeout=20)
    try:
        return mail.sock.version(), mail.sock.cipher()[0]
    finally:
        # UoM may close unauthenticated sessions; do not send IMAP LOGOUT.
        mail.shutdown()
