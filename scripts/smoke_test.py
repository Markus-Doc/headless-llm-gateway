"""Call the running gateway with an evidence-check prompt and a strict JSON schema.

Usage (gateway running, venv active, from the repo root):
    python scripts/smoke_test.py                      # judge only
    python scripts/smoke_test.py judge codex-cli-sol anthropic-haiku
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

EVIDENCE_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "partly_supported", "contradicted", "outdated", "insufficient_evidence"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "quote": {"type": "string", "description": "Shortest passage from the evidence that decides the verdict"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "confidence", "quote", "reason"],
    "additionalProperties": False,
}

SYSTEM = "You check claims against evidence. Use only the evidence given. If it does not decide the claim, say insufficient_evidence."
PROMPT = """Claim: Claude Haiku 5.5 costs USD 0.10 per million input tokens on the Anthropic API.

Evidence (pricing table, retrieved 2026-10-06):
| Model | Input $/1M | Output $/1M |
| Claude Haiku 5.5 | $0.10 | $0.50 |
Prices are for prompts up to 100K tokens ($0.50 / $2.50 beyond)."""


def main(aliases: list[str]) -> int:
    client = OpenAI(base_url="http://127.0.0.1:4000/v1", api_key=os.environ["LITELLM_MASTER_KEY"])
    failures = 0
    for alias in aliases:
        started = time.monotonic()
        try:
            raw = client.chat.completions.with_raw_response.create(
                model=alias,
                messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPT}],
                response_format={"type": "json_schema", "json_schema": {"name": "evidence_check", "schema": EVIDENCE_CHECK_SCHEMA, "strict": True}},
            )
        except Exception as exc:  # report and carry on to the next alias
            failures += 1
            print(f"[{alias}] FAILED after {time.monotonic() - started:.1f}s: {exc}")
            continue
        response = raw.parse()
        verdict = json.loads(response.choices[0].message.content)
        missing = [k for k in EVIDENCE_CHECK_SCHEMA["required"] if k not in verdict]
        print(f"[{alias}] {time.monotonic() - started:.1f}s model={response.model} tokens={response.usage.prompt_tokens}/{response.usage.completion_tokens} "
              f"route={raw.headers.get('x-litellm-model-name')} call_id={raw.headers.get('x-litellm-call-id')} "
              f"cost_usd={raw.headers.get('x-litellm-response-cost-original')}")
        print(f"    {json.dumps(verdict, ensure_ascii=False)}")
        if missing:
            failures += 1
            print(f"    missing keys: {missing}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["judge"]))
