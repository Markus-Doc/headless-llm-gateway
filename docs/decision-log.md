# Decision log

Voice: professional.
Status: working log, opened 2026-10-09 (Australia/Brisbane)
Purpose: record each owner choice on the personal LLM gateway, with the options offered, the recommendation given, the owner's answer and the evidence. This log and the [learning journal](learning-journal.md) feed a portfolio write-up.

Origin: the gateway was split out of the offerings-atlas KB agent planning on 2026-10-09 (Atlas source decision log, build decisions). The KB agents will call AI through this gateway. It also serves the owner's other engineering work and future keystone projects on AWS, Azure and GCP.

## Standing constraints

| Constraint | Source |
| --- | --- |
| Routes that use subscription CLI sessions (`claude -p`, `codex exec`) bind to 127.0.0.1 and are used only by the owner. Hosted or enterprise deployments use API keys only. | Accepted by owner 2026-10-09. Anthropic [legal and compliance](https://code.claude.com/docs/en/legal-and-compliance.md): products and services must use API keys, and third parties may not route requests through Pro or Max credentials on behalf of users. |
| Each machine (laptop and desktop) runs its own gateway on its own localhost, with its own CLI logins. One machine never serves the other over the network. | Follows from the constraint above. Raised 2026-10-09 when the owner said they work on two machines. |
| LiteLLM is pinned to an exact version and installed with hash checking. | PyPI versions 1.82.7 and 1.82.8 were compromised on 24 March 2026 (credential exfiltration). |
| The owner implements the gateway. Claude explains, reviews and writes code only when asked, limited to the piece in question. | Owner direction 2026-10-09. |

## Decisions

| No. | Date | Question | Options offered | Recommendation | Owner answer | Evidence and notes |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-10-09 | Repo name and visibility | `Markus-Doc/llm-gateway`, private | Same | Accepted | Created with `gh repo create --private`. GitHub reports visibility PRIVATE. |
| 2 | 2026-10-09 | Local folder | `D:\Github\0-Working\llm-gateway` or `D:\Github\llm-gateway` | Either | `D:\Github\llm-gateway` | The gateway serves more than one project, so it sits beside them, not inside 0-Working. |
| 3 | 2026-10-09 | Keeping two machines in sync | Not put as options | Git is the sync. `.env` and `.venv` stay per machine. The venv is rebuilt from a hash-pinned lock file. CLI versions are recorded per call. | Owner will pull from GitHub on start and wants a version check on start-up | Start-up check script deferred until there is a lock file to check against. |
| 4 | 2026-10-09 | Python version for the venv | 3.12 or 3.13 (both installed) | 3.12, as LiteLLM proxy dependencies can lag the newest Python | 3.12 (3.12.0 installed) | 3.12.0 is the first 3.12 release. Upgrade if step 3 requirements need a later patch. |
| 6 | 2026-10-09 | LiteLLM version | A: 1.104.2, latest stable, 1 day old. B: 1.104.0, 6 days old. C: wait until 15 October and take the newest stable at least 7 days old. | B, plus a standing upgrade rule: newest stable at least 7 days old, after checking advisories | Delegated to Claude. Taken as B. | 1.82.6 and earlier carry critical advisories fixed later (GHSA-r75f-5x8p-qvmc, GHSA-4xpc-pv4p-pm3w, GHSA-7hp6-4w63-5g45), so the old audited pin is out. 1.104.0 is past every published advisory as of 2026-10-09. PyPI wheels for 1.103.3, 1.104.0 and 1.104.2 have no provenance attestation (integrity API returned 404). Docker images are cosign-signed. A gateway on localhost with no database, SSO or MCP does not use most advised features, so supply-chain risk weighs more than feature fixes. |
| 7 | 2026-10-09 | Lock tooling | Not put as options | pip-tools `pip-compile --generate-hashes`, installed with `pip install --require-hashes` | Delegated to Claude | uv not installed. pip-tools 7.6.1 used, as 7.6.2 was 2 days old under the cooldown rule. The lock is resolved for Windows, which both machines run. |
| 5 | 2026-10-09 | Do raw prompts and outputs (proof capture) go in Git? | In Git. Local only. | Local only | Delegated to Claude with the build | `logs/` is in `.gitignore`. Prompts for KB agents will contain archived source text, which decision 4 of the Atlas log keeps in a separate private repo. The owner can reverse this. |
| 8 | 2026-10-09 | Who builds the rest | Not put as options | None | The owner handed the whole build to Claude after step 3 and will review instead. The owner keeps secrets: `.env` values and CLI logins. | Owner direction 2026-10-09. |
| 9 | 2026-10-09 | Alias design | Not put as options | Route aliases (`claude-cli-haiku`, `claude-cli-sonnet`, `codex-cli-sol`, `anthropic-haiku`) plus role aliases (`judge`) mapped in `router_settings.model_group_alias` | Delegated | Agents ask for roles. Moving a role between subscription and API is one config line. |
| 10 | 2026-10-09 | Default route for `judge` | claude-cli Haiku. Anthropic API Haiku. | claude-cli Haiku, since the PoC runs on the owner's machine and the API key is not yet in `.env` | Delegated | Measured 5 to 8 s per call with schema output. Switch to `anthropic-haiku` for any hosted run. |
| 11 | Open | Use `codex exec` on a ChatGPT login for scripted calls | Keep the route. Drop it. Move Codex to an API key. | Owner to decide | Pending | OpenAI's [authentication page](https://learn.chatgpt.com/docs/auth) says "API keys are still the recommended default for automation" and to use API key auth "for programmatic Codex CLI workflows, such as CI/CD jobs". No statement found that forbids personal local scripts on a ChatGPT login. The ChatGPT plan help article returned 403 and was not read. Route built and tested once. Treat as experimental. |
| 12 | 2026-10-09 | Codex context isolation | Accept the leak. Separate `CODEX_HOME` with its own login. Copy auth.json. | Separate home, logged in by the owner. Copying auth.json was rejected because refresh tokens rotate and two copies would fall out of step. | Pending owner login | `codex exec --ignore-user-config` still loads `~/.codex/AGENTS.md` and the skills list (tested with `codex debug prompt-input` and a yes/no probe). `GATEWAY_CODEX_HOME` support is built in. |
