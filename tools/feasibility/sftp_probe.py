"""NAS SFTP discovery; optional tiny roundtrip inside an explicit personal root.

First contact uses project-local trust-on-first-use; subsequent connections pin
the key. No global SSH configuration or remote service setting is modified.
"""
import argparse
import base64
import hashlib
import io
import json
import posixpath
import re
import uuid
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/feasibility"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--host", default="192.168.124.3")
    parser.add_argument("--root", help="Verified personal-folder SFTP path; enables a tiny reversible roundtrip")
    parser.add_argument("--verify-root-marker", action="store_true", help="Check and remove only the user's empty /home/QQBotRootCheck marker")
    args = parser.parse_args()
    match = re.search(r"账号[：:]\s*([^，,\s]+)[，,]\s*密码[：:]\s*([^）)\s]+)", args.draft.read_text(encoding="utf-8-sig"))
    if not match:
        raise ValueError("NAS credentials not found in the supplied draft")
    username, password = match.groups()
    report = {"host": args.host, "port": 22, "account": username, "protocol": "SFTP"}
    client = paramiko.SSHClient()
    known = OUT / "nas-known-hosts"
    report["host_key_trust"] = "project-local TOFU on first contact; pinned thereafter"
    if not known.exists():
        known.write_text("", encoding="utf-8")
    client.load_system_host_keys()
    client.load_host_keys(str(known))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(args.host, port=22, username=username, password=password,
                       allow_agent=False, look_for_keys=False, timeout=6, banner_timeout=6, auth_timeout=8)
        report["authenticated"] = True
        key = client.get_transport().get_remote_server_key()
        report["host_key_sha256"] = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        with client.open_sftp() as sftp:
            sftp.get_channel().settimeout(10)
            report["sftp_open"] = True
            report["login_directory"] = sftp.normalize(".")
            report["own_home_entries"] = [{"name": e.filename, "mode": oct(e.st_mode)} for e in sftp.listdir_attr(".")]
            if args.verify_root_marker:
                marker = "/home/QQBotRootCheck"
                if sftp.listdir(marker):
                    raise ValueError("Root marker is not empty; refusing to remove it")
                sftp.rmdir(marker)
                report["ui_personal_root_verified"] = "/home"
                report["user_created_empty_marker_removed"] = True
            if args.root:
                root = sftp.normalize(args.root)
                report["personal_root"] = root
                testdir = posixpath.join(root, ".qq-bot-probe-" + uuid.uuid4().hex)
                local = b"QQ_BOT_SFTP_ROUNDTRIP_OK\n"
                sftp.mkdir(testdir)
                temporary = posixpath.join(testdir, "probe.part")
                final = posixpath.join(testdir, "probe.txt")
                try:
                    sftp.putfo(io.BytesIO(local), temporary, file_size=len(local), confirm=True)
                    sftp.rename(temporary, final)
                    with sftp.open(final, "rb") as stream:
                        returned = stream.read(4096)
                    report["roundtrip"] = {"bytes": len(returned), "sha256": hashlib.sha256(returned).hexdigest(), "matched": returned == local}
                    report["roundtrip"]["success"] = returned == local
                    if returned != local:
                        raise IOError("SFTP roundtrip hash/content mismatch")
                finally:
                    for path in (temporary, final):
                        try:
                            sftp.remove(path)
                        except FileNotFoundError:
                            pass
                    sftp.rmdir(testdir)
                    report["temporary_files_removed"] = True
            report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:500].replace(password, "<redacted>")
    finally:
        client.close()
    name = "sftp-root-mapping.json" if args.verify_root_marker else "sftp-probe.json"
    (OUT / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
