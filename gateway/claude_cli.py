"""LiteLLM provider `claude-cli`: runs `claude -p` on the owner's Claude subscription.

Registered in config.yaml as `claude-cli/<model id>`. Localhost and owner use
only (see docs/decision-log.md, standing constraints).

Lock-down, all verified against Claude Code 2.1.295 on Windows:
- no tools (`--tools ""`), no MCP servers, `--restricted`, permission mode dontAsk
- one turn, no saved session, empty temporary working folder
- `--system-prompt` replaces Claude Code's own prompt, so no CLAUDE.md, memory
  or skills reach the model (about 850 input tokens instead of 3,600)
- ANTHROPIC_API_KEY and ANTHROPIC_AUTH_TOKEN are removed from the child
  environment, because in -p mode an API key overrides the subscription login
- claude.exe is called directly. The npm `claude.cmd` shim would route the
  arguments through cmd.exe, which re-parses quotes and the empty `--tools ""`.

`--bare` is not used: it skips credential reads and the subscription login
then fails with "Not logged in".
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

DEFAULT_SYSTEM_PROMPT = "You are a careful assistant. Answer the request directly and accurately."
DROP_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


@functools.lru_cache(maxsize=1)
def claude_exe() -> str:
    override = os.environ.get("CLAUDE_CLI_EXE")
    if override:
        return override
    found = shutil.which("claude")
    if not found:
        raise CustomLLMError(status_code=500, message="claude CLI not found on PATH. Set CLAUDE_CLI_EXE.")
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".ps1", "") and os.name == "nt":
        native = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if native.exists():
            return str(native)
    return str(path)


@functools.lru_cache(maxsize=1)
def cli_facts() -> dict:
    """Version and auth method, checked once per gateway process with the child environment."""
    env = common.child_env(DROP_ENV)
    version = subprocess.run([claude_exe(), "--version"], capture_output=True, env=env, timeout=60).stdout.decode().strip()
    status_raw = subprocess.run([claude_exe(), "auth", "status"], capture_output=True, env=env, timeout=60).stdout
    try:
        status = json.loads(status_raw)
    except json.JSONDecodeError:
        status = {}
    return {
        "cli_version": version,
        "auth_logged_in": status.get("loggedIn"),
        "auth_method": status.get("authMethod"),
        "api_provider": status.get("apiProvider"),
    }


def build_args(model: str, system: str, schema: dict | None) -> list[str]:
    args = [
        claude_exe(), "-p",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", system or DEFAULT_SYSTEM_PROMPT,
        "--tools", "",
        "--disallowedTools", "mcp__*",
        "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--restricted",
        "--max-turns", "1",
        "--no-session-persistence",
    ]
    if schema:
        args += ["--json-schema", json.dumps(schema)]
    return args


class ClaudeCLI(common.CLIProvider):
    def completion(self, model: str, messages: list, api_base: str, custom_prompt_dict: dict, model_response: ModelResponse,
                   print_verbose, encoding, api_key, logging_obj, optional_params: dict, acompletion=None,
                   litellm_params=None, logger_fn=None, headers={}, timeout=None, client=None) -> ModelResponse:
        system, prompt = common.flatten_messages(messages)
        schema = common.extract_schema(optional_params)
        proof = common.new_proof("claude-cli", model, system, prompt, schema, litellm_params)

        facts = cli_facts()
        proof.update(facts)
        if not facts["auth_logged_in"]:
            raise CustomLLMError(status_code=503, message="claude CLI is not logged in. Run `claude` once and sign in.")

        with common.empty_workdir() as workdir:
            stdout, stderr, returncode, seconds = common.run_cli(build_args(model, system, schema), prompt, common.child_env(DROP_ENV), workdir)
        proof.update({"duration_s": round(seconds, 2), "returncode": returncode, "raw_stdout": stdout.decode("utf-8", "replace"),
                      "raw_stderr": stderr.decode("utf-8", "replace")[-2000:]})

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            common.write_proof(proof)
            raise CustomLLMError(status_code=502, message=f"claude CLI returned no JSON (exit {returncode}): {stderr.decode('utf-8', 'replace')[-500:]}")

        usage = result.get("usage") or {}
        prompt_tokens = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        proof.update({
            "session_id": result.get("session_id"),
            "models_used": list((result.get("modelUsage") or {}).keys()),
            "is_error": result.get("is_error"),
            "subtype": result.get("subtype"),
            "usage": usage,
            "notional_cost_usd": result.get("total_cost_usd"),
        })
        common.write_proof(proof)

        if result.get("is_error") or result.get("subtype") != "success":
            raise CustomLLMError(status_code=502, message=f"claude CLI failed: subtype={result.get('subtype')} result={str(result.get('result'))[:500]}")
        if schema:
            if result.get("structured_output") is None:
                raise CustomLLMError(status_code=502, message="claude CLI returned no structured_output for the requested schema")
            content = json.dumps(result["structured_output"], ensure_ascii=False)
        else:
            content = result.get("result") or ""

        return common.fill_response(model_response, model, content, prompt_tokens, usage.get("output_tokens", 0))


handler = ClaudeCLI()
