"""Minimal, opt-in SDK inference probe. Never sends chat records or credentials."""
from __future__ import annotations

import json
import time
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    out = root / "artifacts/feasibility"
    work = out / "agent-work"
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    report = {"sdk": "openai-codex==0.162.1", "requested_model": "gpt-6-luna"}
    try:
        # Keep the pinned runtime and normal login; use an empty working directory.
        config = CodexConfig(cwd=str(work))
        with Codex(config) as client:
            account = client.account()
            report["authenticated"] = account.account is not None
            report["account_type"] = getattr(account.account, "type", None)
            models = client.models()
            report["models"] = [getattr(m, "model", None) for m in models.data]
            thread = client.thread_start(
                cwd=str(work), model="gpt-6-luna", sandbox=Sandbox.read_only,
                approval_mode=ApprovalMode.deny_all, ephemeral=True,
                developer_instructions="This is a connectivity test. Do not use any tools, read files, access network, or start agents. Answer the user literally.",
            )
            result = thread.run("Reply with exactly QQ_BOT_SDK_OK. Do not use tools.", effort="low")
            report["final_response"] = result.final_response
            report["turn_status"] = result.status.value
            report["turn_error"] = str(result.error) if result.error else None
            report["item_types"] = [getattr(item, "type", type(item).__name__) for item in result.items]
            report["success"] = result.status.value == "completed" and (result.final_response or "").strip() == "QQ_BOT_SDK_OK"
    except Exception as exc:
        report["success"] = False
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:1600]
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    (out / "codex-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
