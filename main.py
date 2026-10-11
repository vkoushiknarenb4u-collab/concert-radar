"""Command-line scan entry point: python main.py"""

from __future__ import annotations

import argparse
from config import load_settings
from scanner import run_scan


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan for concerts matching your saved artists.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="refresh fictional demo data without calling APIs")
    mode.add_argument("--live", action="store_true", help="run the configured Ticketmaster scan")
    args = parser.parse_args()
    override = True if args.demo else False if args.live else None
    result = run_scan(load_settings(), demo_mode=override)
    print(f"Concert Radar scan: {result['status']}")
    print(result["message"])
    print(f"Events found: {result.get('events_found', 0)} | New: {result.get('events_new', 0)} | Emails sent: {result.get('notifications_sent', 0)}")
    return 0 if result["status"] in {"success", "needs_setup", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
