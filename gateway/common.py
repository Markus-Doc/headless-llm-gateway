"""Shared plumbing for the CLI-backed providers.

Each provider turns an OpenAI-style chat request into one locked-down CLI run:
no shell, an empty temporary working folder, a cleaned environment, the prompt
on stdin and a JSON result on stdout. Every call appends one proof record to
logs/proof-YYYY-MM-DD.jsonl.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import litellm
from litellm import CustomLLM
from litellm.llms.custom_llm import CustomLLMError
from litellm.types.utils import ModelResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = Path(os.environ.get("GATEWAY_LOG_DIR", REPO_ROOT / "logs"))
CLI_TIMEOUT_SECONDS = float(os.environ.get("GATEWAY_CLI_TIMEOUT", "300"))

# Subscription sessions are rate limited per account. Cap parallel CLI runs so a
# burst from an agent queues here instead of tripping the provider's limits.
_cli_slots = threading.BoundedSemaphore(int(os.environ.get("GATEWAY_CLI_CONCURRENCY", "2")))
_log_lock = threading.Lock()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part.get("type") != "text":
            raise CustomLLMError(status_code=400, message=f"CLI routes accept text only, got a '{part.get('type')}' part")
        parts.append(part["text"])
    return "\n".join(parts)


def flatten_messages(messages: list[dict]) -> tuple[str, str]:
    """Split chat messages into (system text, prompt text).

    The CLIs take one prompt per run, so a multi-turn history is written out as
    a labelled transcript. A single user message passes through unchanged.
    """
    system = [_content_text(m.get("content")) for m in messages if m.get("role") in ("system", "developer")]
    turns = [m for m in messages if m.get("role") not in ("system", "developer")]
    if not turns:
        raise CustomLLMError(status_code=400, message="Request has no user message")
    if len(turns) == 1 and turns[0].get("role") == "user":
        prompt = _content_text(turns[0].get("content"))
    else:
        prompt = "\n\n".join(f"{m['role'].upper()}:\n{_content_text(m.get('content'))}" for m in turns)
    return "\n\n".join(s for s in system if s), prompt


def extract_schema(optional_params: dict) -> dict | None:
    """Return the JSON schema from an OpenAI response_format, if one was sent."""
    response_format = optional_params.get("response_format")
    if not response_format:
        return None
    if hasattr(response_format, "model_json_schema"):  # a Pydantic class passed through the SDK
        return response_format.model_json_schema()
    kind = response_format.get("type")
    if kind == "json_schema":
        return response_format["json_schema"]["schema"]
    if kind == "json_object":
        return {"type": "object"}
    return None


def child_env(drop: tuple[str, ...], extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(extra or {})
    return env


def run_cli(args: list[str], prompt: str, env: dict[str, str], workdir: str) -> tuple[bytes, bytes, int, float]:
    """Run one CLI process with the prompt on stdin. No shell is involved."""
    with _cli_slots:
        started = time.monotonic()
        try:
            result = subprocess.run(
                args,
                input=prompt.encode("utf-8"),
                capture_output=True,
                cwd=workdir,
                env=env,
                timeout=CLI_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise CustomLLMError(status_code=504, message=f"CLI run exceeded {CLI_TIMEOUT_SECONDS:.0f}s") from exc
        return result.stdout, result.stderr, result.returncode, time.monotonic() - started


def empty_workdir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="llm-gateway-")


def write_proof(record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"proof-{dt.date.today().isoformat()}.jsonl"
    line = json.dumps(record, ensure_ascii=False)
    with _log_lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def new_proof(route: str, model: str, system: str, prompt: str, schema: dict | None, litellm_params: dict | None) -> dict:
    return {
        "proof_id": str(uuid.uuid4()),
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "route": route,
        "requested_model": model,
        "litellm_call_id": (litellm_params or {}).get("litellm_call_id"),
        "system_sha256": sha256_text(system),
        "prompt_sha256": sha256_text(prompt),
        "schema_sha256": sha256_text(json.dumps(schema, sort_keys=True)) if schema else None,
    }


def fill_response(model_response: ModelResponse, model: str, content: str, prompt_tokens: int, completion_tokens: int) -> ModelResponse:
    # Callers match a response to its proof record through litellm_call_id, which
    # the proxy returns as the x-litellm-call-id response header.
    model_response.model = model
    model_response.choices[0].message.content = content  # type: ignore[union-attr]
    model_response.choices[0].finish_reason = "stop"
    setattr(
        model_response,
        "usage",
        litellm.Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=prompt_tokens + completion_tokens),
    )
    return model_response


class CLIProvider(CustomLLM):
    """Base class: sync completion runs the CLI, async wraps it in a worker thread."""

    async def acompletion(self, *args, **kwargs) -> ModelResponse:  # type: ignore[override]
        return await asyncio.to_thread(self.completion, *args, **kwargs)

    def streaming(self, *args, **kwargs):  # type: ignore[override]
        raise CustomLLMError(status_code=400, message="CLI routes do not stream. Send stream=false.")

    async def astreaming(self, *args, **kwargs):  # type: ignore[override]
        raise CustomLLMError(status_code=400, message="CLI routes do not stream. Send stream=false.")
