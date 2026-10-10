"""LiteLLM provider `codex-cli`: runs `codex exec` on the owner's ChatGPT login.

Registered in config.yaml as `codex-cli/<model slug>`. Localhost and owner use
only. OpenAI recommends API keys for automation (decision log, open item), so
treat this route as experimental.

Lock-down, verified against Codex CLI 0.161.0 on Windows:
- read-only sandbox, no saved session, no user config.toml or exec rules
- empty temporary working folder, outside any Git repo
- OPENAI_API_KEY and CODEX_API_KEY removed from the child environment
- node.exe runs codex.js directly, bypassing the codex.cmd shim and cmd.exe

Known gap: `--ignore-user-config` does not stop Codex loading
$CODEX_HOME/AGENTS.md and the skills list (about 15k input tokens). Set
GATEWAY_CODEX_HOME to a folder used only by the gateway and log in there once
(see README) to cut that context out. Each proof record says whether it was.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
from pathlib import Path

from litellm.llms.custom_llm import CustomLLMError
from litellm.types.utils import ModelResponse

from gateway import common

DROP_ENV = ("OPENAI_API_KEY", "CODEX_API_KEY")
REASONING_EFFORT = os.environ.get("GATEWAY_CODEX_EFFORT", "low")


@functools.lru_cache(maxsize=1)
def codex_command() -> tuple[str, ...]:
    """Command prefix that starts Codex without going through cmd.exe."""
    js_override = os.environ.get("CODEX_CLI_JS")
    if js_override:
        return (os.environ.get("NODE_EXE") or shutil.which("node") or "node", js_override)
    found = shutil.which("codex")
    if not found:
        raise CustomLLMError(status_code=500, message="codex CLI not found on PATH. Set CODEX_CLI_JS.")
    path = Path(found)
    if os.name == "nt" and path.suffix.lower() in (".cmd", ".ps1", ""):
        js = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = path.parent / "node.exe"
        if js.exists():
            return (str(node) if node.exists() else (shutil.which("node") or "node"), str(js))
    return (str(path),)


def codex_env() -> dict[str, str]:
    home = os.environ.get("GATEWAY_CODEX_HOME")
    return common.child_env(DROP_ENV, {"CODEX_HOME": home} if home else None)


@functools.lru_cache(maxsize=1)
def cli_facts() -> dict:
    env = codex_env()
    version = subprocess.run([*codex_command(), "--version"], capture_output=True, env=env, timeout=60).stdout.decode().strip()
    status = subprocess.run([*codex_command(), "login", "status"], capture_output=True, env=env, timeout=60)
    status_text = (status.stdout or status.stderr).decode("utf-8", "replace").strip()
    return {
        "cli_version": version,
        "auth_logged_in": status.returncode == 0 and "Logged in" in status_text,
        "auth_method": status_text,
        "codex_home_isolated": bool(os.environ.get("GATEWAY_CODEX_HOME")),
    }


def build_args(model: str, workdir: str, schema_path: str | None) -> list[str]:
    args = [
        *codex_command(), "exec",
        "--json",
        "-s", "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-C", workdir,
        "-m", model,
        "-c", f'model_reasoning_effort="{REASONING_EFFORT}"',
    ]
    if schema_path:
        args += ["--output-schema", schema_path]
    return args + ["-"]  # prompt on stdin


def parse_events(stdout: bytes) -> dict:
    parsed: dict = {"thread_id": None, "message": None, "usage": {}, "errors": []}
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "thread.started":
            parsed["thread_id"] = event.get("thread_id")
        elif kind == "item.completed" and event.get("item", {}).get("type") == "agent_message":
            parsed["message"] = event["item"].get("text")
        elif kind == "turn.completed":
            parsed["usage"] = event.get("usage") or {}
        elif kind in ("error", "turn.failed"):
            parsed["errors"].append(event)
    return parsed


class CodexCLI(common.CLIProvider):
    def completion(self, model: str, messages: list, api_base: str, custom_prompt_dict: dict, model_response: ModelResponse,
                   print_verbose, encoding, api_key, logging_obj, optional_params: dict, acompletion=None,
                   litellm_params=None, logger_fn=None, headers={}, timeout=None, client=None) -> ModelResponse:
        system, prompt = common.flatten_messages(messages)
        schema = common.extract_schema(optional_params)
        proof = common.new_proof("codex-cli", model, system, prompt, schema, litellm_params)

        facts = cli_facts()
        proof.update(facts)
        if not facts["auth_logged_in"]:
            raise CustomLLMError(status_code=503, message=f"codex CLI is not logged in: {facts['auth_method']}")

        # codex exec has no system prompt flag, so instructions go in front of the task.
        full_prompt = f"Instructions:\n{system}\n\nTask:\n{prompt}" if system else prompt

        with common.empty_workdir() as workdir, common.empty_workdir() as schema_dir:
            schema_path = None
            if schema:
                schema_path = str(Path(schema_dir) / "schema.json")
                Path(schema_path).write_text(json.dumps(schema), encoding="utf-8")
            stdout, stderr, returncode, seconds = common.run_cli(build_args(model, workdir, schema_path), full_prompt, codex_env(), workdir)

        events = parse_events(stdout)
        usage = events["usage"]
        proof.update({
            "duration_s": round(seconds, 2),
            "returncode": returncode,
            "session_id": events["thread_id"],
            "models_used": [model],  # codex exec events do not echo the model, so this is the -m value
            "reasoning_effort": REASONING_EFFORT,
            "usage": usage,
            "raw_stdout": stdout.decode("utf-8", "replace"),
            "raw_stderr": stderr.decode("utf-8", "replace")[-2000:],
        })
        common.write_proof(proof)

        if returncode != 0 or events["errors"] or events["message"] is None:
            detail = events["errors"][-1] if events["errors"] else stderr.decode("utf-8", "replace")[-500:]
            raise CustomLLMError(status_code=502, message=f"codex exec failed (exit {returncode}): {detail}")

        content = events["message"]
        if schema:
            try:
                content = json.dumps(json.loads(content), ensure_ascii=False)
            except json.JSONDecodeError as exc:
                raise CustomLLMError(status_code=502, message="codex exec returned text that is not JSON despite --output-schema") from exc

        return common.fill_response(model_response, model, content, usage.get("input_tokens", 0), usage.get("output_tokens", 0))


handler = CodexCLI()
