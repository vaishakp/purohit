"""Write a local control command JSON file for the controlled static monitor.

This helper is useful for testing the command queue locally, or for trusted
site-specific command ingress wrappers that need to write a signed command file.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import time
import uuid

from reanalyze.control_queue import canonical_payload


def sign_command(command: dict, secret_file: Path | None) -> dict:
    """Add an HMAC-SHA256 signature when a secret file is supplied."""

    if secret_file is None:
        return command
    secret = secret_file.expanduser().read_text().strip().encode("utf-8")
    signed = dict(command)
    signed["signature"] = hmac.new(secret, canonical_payload(signed), hashlib.sha256).hexdigest()
    return signed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write a Purohit control command JSON file.")
    parser.add_argument("--inbox", required=True, type=Path, help="Command inbox directory polled by the monitor agent.")
    parser.add_argument("--action", choices=["submit_event", "refresh_status"], required=True)
    parser.add_argument("--event", help="Event to submit when action=submit_event.")
    parser.add_argument("--config-path", help="Optional explicit project-local INI path for the selected event.")
    parser.add_argument("--requested-by", default="unknown", help="Free-form requester label for audit logs.")
    parser.add_argument("--secret-file", type=Path, help="Optional shared secret for signing the command.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.action == "submit_event" and not args.event:
        raise SystemExit("--event is required for submit_event")

    args.inbox.mkdir(parents=True, exist_ok=True)
    command = {
        "action": args.action,
        "created_at": time.time(),
        "nonce": uuid.uuid4().hex,
        "requested_by": args.requested_by,
    }
    if args.event:
        command["event"] = args.event
    if args.config_path:
        command["config_path"] = args.config_path

    command = sign_command(command, args.secret_file)
    path = args.inbox / f"{int(time.time())}-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(command, indent=2, sort_keys=True) + "\n")
    print(path)


if __name__ == "__main__":
    main()
