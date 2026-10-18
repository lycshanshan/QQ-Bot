"""Read-only NAS authentication/share discovery using credentials in the user draft.

Credentials remain in memory and are never written to results or command arguments.
This probe does not enable services, upload files, or enumerate file contents.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

from smb.SMBConnection import SMBConnection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--host", default="192.168.124.3")
    args = parser.parse_args()
    draft = args.draft.read_text(encoding="utf-8-sig")
    credentials = re.search(r"账号[：:]\s*([^，,\s]+)[，,]\s*密码[：:]\s*([^）)\s]+)", draft)
    if credentials is None:
        raise ValueError("Could not locate NAS account in the supplied draft")
    username, password = credentials.groups()
    auth_messages: list[str] = []

    class AuthLog(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if "Authentication" in message:
                auth_messages.append(message.replace(password, "<redacted>"))

    logging.getLogger().addHandler(AuthLog())
    logging.getLogger().setLevel(logging.INFO)
    connection = SMBConnection(username, password, "QQBOTPROBE", args.host,
                               use_ntlm_v2=True, is_direct_tcp=True)
    report: dict = {"host": args.host, "protocol": "SMB", "port": 445, "account": username}
    try:
        report["authenticated"] = connection.connect(args.host, 445, timeout=8)
        report["smb2"] = connection.isUsingSMB2
        if not report["authenticated"] or not report["smb2"]:
            raise RuntimeError("Authentication or SMB2 negotiation failed")
        report["shares"] = [{"name": share.name, "type": share.type} for share in connection.listShares(timeout=8)]
        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc).splitlines()[0][:240].replace(password, "<redacted>")
    finally:
        connection.close()
    report["authentication_messages"] = auth_messages
    out = Path(__file__).resolve().parents[2] / "artifacts/feasibility/nas-probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
