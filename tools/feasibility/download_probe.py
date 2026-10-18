"""Download ONLY small media from the user's explicit test/quoted messages.

No auto-download listener. This one-shot probe runs after a quote is received.
"""
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/feasibility"
MAX_BYTES = 2 * 1024 * 1024


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = ("qq.com", "qq.com.cn", "qpic.cn")
    if parsed.scheme != "https" or parsed.username or not any(host == d or host.endswith("." + d) for d in allowed):
        raise ValueError("Unexpected test media origin")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def main() -> None:
    events = [json.loads(line)["event"] for line in (OUT / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    report = []
    opener = urllib.request.build_opener(SafeRedirect())
    for label, media_type in (("quoted-image", "image"), ("quoted-group-file", "file")):
        candidates = []
        for event in events:
            reply = event.get("reply") or {}
            if media_type == "file" and event.get("group_id") != 1049251004:
                continue
            for segment in reply.get("message", []):
                if segment["type"] == media_type and segment["data"].get("url"):
                    candidates.append((event, segment))
        if not candidates:
            report.append({"test": label, "success": False, "reason": "No matching quoted test media with URL"})
            continue
        event, segment = candidates[-1]
        item = {"test": label, "trigger_message_id": event["message_id"], "referenced_message_id": event["reply"]["message_id"]}
        try:
            url = segment["data"]["url"]
            validate_url(url)
            with opener.open(url, timeout=15) as response:
                blob = response.read(MAX_BYTES + 1)
            if len(blob) > MAX_BYTES:
                raise ValueError("Test media exceeds 2 MiB limit")
            path = OUT / f"download-{label}.bin"
            path.write_bytes(blob)
            item.update(success=True, bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
            if media_type == "image":
                item["recognized_image_signature"] = blob.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF"))
                item["success"] = item["recognized_image_signature"]
        except Exception as exc:
            item.update(success=False, error_type=type(exc).__name__)
        report.append(item)
    (OUT / "download-probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
