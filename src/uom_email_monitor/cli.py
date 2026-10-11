"""Safe command-line startup and read-only configuration checks."""

import argparse
import signal

from . import main as monitor
from .config import ConfigurationError, load_config
from .lock import AlreadyRunningError, monitor_lock
from .tls import probe_imap_tls


def _stop(signum, frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description="UoM Email Telegram Monitor")
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--check-config", action="store_true",
                        help="Validate config and read existing state; no network or writes")
    checks.add_argument("--check-imap-tls", action="store_true",
                        help="Check TLS from this host WITHOUT credentials or login")
    parser.add_argument("--host", default="mail.uom.lk", help="Host for the TLS-only probe")
    parser.add_argument("--port", type=int, default=993, help="Port for the TLS-only probe")
    args = parser.parse_args(argv)
    try:
        if args.check_imap_tls:
            version, cipher = probe_imap_tls(args.host, args.port)
            print(f"IMAP TLS verified: {version}, {cipher}; no login performed.")
            return 0
        config = load_config()
        monitor.configure(config)
        if args.check_config:
            state = monitor.load_state()
            print("Configuration valid; existing state loaded." if state is not None
                  else "Configuration valid; no state exists (first run will baseline INBOX).")
            return 0
        signal.signal(signal.SIGTERM, _stop)
        with monitor_lock(config.state_file):
            monitor.start_monitor()
        return 0
    except KeyboardInterrupt:
        return 0
    except (ConfigurationError, AlreadyRunningError) as exc:
        print(str(exc))  # These exceptions contain only fixed, safe diagnostics.
        return 1
    except Exception as exc:
        print(f"Startup/check failed ({type(exc).__name__}); inspect configuration and state.")
        return 1
