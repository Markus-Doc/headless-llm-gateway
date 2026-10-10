# Learning journal

What the owner did at each step, what went wrong and what was learnt. Written as it happens, corrected by the owner. Choices go in the [decision log](decision-log.md).

## 2026-10-09

### Step 1: concepts

- LiteLLM has two forms. The SDK is a Python library used inside one program. The proxy is an OpenAI-compatible HTTP server built on the SDK, configured by `config.yaml`. Agents only talk to the proxy.
- A model alias (`model_name`, for example `judge`) is what clients ask for. `litellm_params.model` decides where the call really goes. Changing a route means editing that one value, with no agent code touched.
- Understanding check. Q: to move `judge` from Haiku to Sonnet, what changes? A: the config.yaml value. Q: why 127.0.0.1 and not 0.0.0.0? A: to keep it local and personal so it stays within the terms. Refinement: 0.0.0.0 listens on every network interface, so anyone on the same wifi could use the subscription routes.

### Step 2: repo and environment

- Created `D:\Github\llm-gateway`, ran `git init -b main` and built a venv with `py -3.12 -m venv .venv`.
- Went wrong: typed `python -v` (lowercase) instead of `python -V` or `--version`. Lowercase is verbose mode and opens the interactive prompt (`>>>`). Ctrl+C only raises `KeyboardInterrupt` there. Leave with `exit()` or Ctrl+Z then Enter on Windows. The venv itself had been created correctly.
- Wrote `.gitignore` with `.venv/`, `.env` and `__pycache__/`. Confirmed with `git status` that only `.gitignore` showed as untracked.
- First commit `090a4b9`, then `gh repo create Markus-Doc/llm-gateway --private --source . --remote origin --push`. `--private` is the flag that matters most. Leaving it out makes the repo public.
- After step 2 the owner handed general set-up work (Git, Python packaging, version research) to Claude, as they already know it. The owner drives everything specific to LiteLLM.

### Step 3: version and lock file (done by Claude)

- Checked LiteLLM's March 2026 security update, the GitHub security advisories and the PyPI release history. Choice and evidence in decision 6.
- Hash checking protects against a file being swapped after it was locked. It does not protect against locking a version that was already malicious. That is why the version choice and a release cooldown matter as well.
- `requirements.in` states the one direct dependency. `requirements.txt` is the generated lock with every dependency and its SHA-256.
- The lock resolved to 116 packages (about 3,000 lines with hashes). Resolving and hashing took over 10 minutes. The owner noted afterwards that they would have liked to watch this run to see what LiteLLM pulls in. Lesson for the write-up: show long installs, don't just report them.
- Installed with `pip install --require-hashes`. Checked every `.pth` file in the venv, the start-up hook 1.82.8 abused: only setuptools' `distutils-precedence.pth` and `pywin32.pth`, both expected.

### Steps 4 to 7: the build (done by Claude, owner reviewing)

The owner handed over the whole build at this point and kept the secrets.

Found in LiteLLM's source before writing anything:
- The proxy's default `--host` is `0.0.0.0` (`proxy_cli.py:707`). The start script always passes 127.0.0.1.
- The proxy calls `load_dotenv()`, so `.env` in the folder it starts from is read automatically.
- A custom handler gets the model with the provider prefix removed (`claude-cli/claude-haiku-5-5` arrives as `claude-haiku-5-5`) and gets `response_format` intact in `optional_params`.
- `custom_handler: gateway.claude_cli.handler` is loaded from a file path next to `config.yaml`, so imports between handler modules need the repo root on `PYTHONPATH`.

Found by probing the CLIs from Python:
- `claude` and `codex` on PATH are npm `.cmd` shims. Through `subprocess` those go via cmd.exe, which re-parses quotes. Calling `claude.exe` and `node codex.js` directly avoids it. The empty `--tools ""` argument survived intact this way.
- `claude -p` with the locked-down flags took about 4 s, not the 13 to 17 s expected. `--json-schema` makes the model answer through a structured-output tool, so `num_turns` is 2 even with `--max-turns 1`, and it still succeeds.
- UTF-8 round-trips correctly when stdin and stdout are handled as bytes. An early garbled degree sign came from the probe's console, not the CLI.
- `--system-prompt` replaces Claude Code's own prompt. Input fell from about 3,600 tokens to about 850, and the model reported no CLAUDE.md or skills in context.
- `--bare` breaks subscription login ("Not logged in") because it skips credential reads.
- `codex exec --ignore-user-config` still loads `~/.codex/AGENTS.md` and the skills list (about 15k input tokens). There is no flag for it. The fix is a gateway-only `CODEX_HOME`.
- `codex exec --json` events do not say which model ran, so the route always passes `-m`.

Results with the gateway running on 127.0.0.1:4000 (`netstat` shows it listening on 127.0.0.1 only):
- An evidence-check prompt with a strict five-verdict schema returned valid JSON from `judge` (7 s), `claude-cli-sonnet` (4.5 s) and `codex-cli-sol` (11 s).
- `anthropic-haiku` returns 401 "Missing Anthropic API Key" until the key is in `.env`.
- No key gives 500 (a LiteLLM error-handler bug, see README), a wrong key gives 400, the right key works.
- Each call wrote a proof record. `x-litellm-call-id` in the response matches `litellm_call_id` in the record.
