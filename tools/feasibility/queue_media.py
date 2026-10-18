"""Queue tiny, labelled media tests to the two explicit test targets."""
import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/feasibility"


def main() -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    # Small synthetic PNG for transport validation, no user media required.
    width, height = 128, 64
    pixels = b"".join(b"\x00" + bytes((40, 130, 210)) * width for _ in range(height))
    image = OUT / "transport-test.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", width, height, 8, 2, 0, 0, 0))
                      + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
    document = OUT / "qq-bot-feasibility.txt"
    document.write_text("QQ_BOT_FILE_TEST_OK\nThis file contains no private data.\n", encoding="utf-8")
    for kind, target_key, target in (("private", "user_id", 2430023288), ("group", "group_id", 1049251004)):
        requests = {
            f"010-{kind}-mixed": {
                "action": f"send_{kind}_msg", "params": {target_key: target, "message": [
                    {"type": "text", "data": {"text": "【可行性测试】图片前的文字\n"}},
                    {"type": "image", "data": {"file": image.resolve().as_uri()}},
                    {"type": "text", "data": {"text": "\n图片后的文字"}},
                ]},
            },
            f"011-{kind}-file": {
                "action": f"upload_{kind}_file", "params": {
                    target_key: target, "file": str(document.resolve()), "name": document.name,
                },
            },
        }
        for name, request in requests.items():
            path = OUT / "requests" / f"{name}.json"
            if not path.exists():
                path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    print("Queued labelled mixed-message and file tests; repeated execution does not resend.")


if __name__ == "__main__":
    main()
