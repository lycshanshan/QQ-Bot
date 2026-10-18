"""Synthetic image, structured output, conversation and per-turn model tests."""
import json
import time
from pathlib import Path

from openai_codex import ApprovalMode, Codex, CodexConfig, LocalImageInput, Sandbox, TextInput

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/feasibility"
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"color": {"type": "string"}, "memo": {"type": "string"}},
    "required": ["color", "memo"],
}


def main() -> None:
    report = {"input": "synthetic blue PNG only", "turns": []}
    started = time.monotonic()
    try:
        with Codex(CodexConfig(cwd=str(OUT / "agent-work"))) as client:
            thread = client.thread_start(
                model="gpt-6-luna", cwd=str(OUT / "agent-work"), ephemeral=True,
                sandbox=Sandbox.read_only, approval_mode=ApprovalMode.deny_all,
                developer_instructions="Connectivity test only. Do not call tools or start agents. Describe provided images literally. Follow the response JSON schema.",
            )
            inputs = [TextInput("Identify the dominant color in English. Remember memo QQ_CTX_314159 and return it in the memo field."),
                      LocalImageInput(str(OUT / "transport-test.png"))]
            first = thread.run(inputs, output_schema=SCHEMA, effort="low")
            report["turns"].append({"model": "gpt-6-luna", "status": first.status.value,
                                    "response": first.final_response, "error": str(first.error) if first.error else None})
            second = thread.run("Recall the color and exact memo from my previous message.", model="gpt-6.1-sol",
                                output_schema=SCHEMA, effort="low")
            report["turns"].append({"model": "gpt-6.1-sol", "status": second.status.value,
                                    "response": second.final_response, "error": str(second.error) if second.error else None})
            report["success"] = all(t["status"] == "completed" and
                json.loads(t["response"])["memo"] == "QQ_CTX_314159" and
                "blue" in json.loads(t["response"])["color"].lower() for t in report["turns"])
    except Exception as exc:
        report["success"] = False
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:1000]
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    (OUT / "codex-media-probe.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
