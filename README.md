# dsh-independence

[![ci](https://github.com/MisterTK/dsh-independence/actions/workflows/ci.yml/badge.svg)](https://github.com/MisterTK/dsh-independence/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Run [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) with **no ties to DeepSeek**: no telemetry, no metadata side-channels, no data leaving your machine except LLM calls to providers *you* configure.

## Why this exists

deepseek-harness is genuinely modular and its data flows are documented — but the shipped defaults are not egress-free. As of `0.1.2-alpha.1`:

| Channel | Default | What leaves your machine |
|---|---|---|
| Session telemetry (`session-telemetry-otel`) | **ON**, feedback-gated | Running `/feedback` uploads your raw, unredacted session records (messages, tool results, cwd, system prompts) to a hardcoded collector, `harness-telemetry.deepseeksvc.com` |
| Request headers (`llm-deepseek`) | **ON** | `x-deepseek-harness-user-id` (stable anonymous UUID) + `x-deepseek-harness-session-id` on every DeepSeek API request; not configurable |
| `dsh_plugin_packages` wire extension | **ON** | Your complete active plugin inventory (names + versions) inside every DeepSeek chat request |
| `dsh_session_log` wire extension | off | Would stream your full session log inside DeepSeek chat requests if a deployment enables it |
| `web_search` | **ON** | Query text to DeepSeek's Messages endpoint |

This project turns all of that off with one command, routes models through your own providers, and — because the harness is pre-1.0 and changes fast — gives you a per-upgrade **egress audit** that catches anything new before you trust a release.

## Quick start (no agent required)

Prerequisite: [uv](https://docs.astral.sh/uv/), plus a deepseek-harness checkout.

```sh
uv run https://raw.githubusercontent.com/MisterTK/dsh-independence/main/skills/dsh-independence/scripts/independence.py init
```

That writes the lockdown patch to `~/.dsh/cordis.patch.yml` (the harness's home patch layer — it survives upgrades) and records an audit baseline. Then open the patch and fill in the commented `llm-pi-ai` provider template with your own routes.

After every harness upgrade:

```sh
uv run .../independence.py audit    # exit 0 = surface unchanged since your accepted baseline
uv run .../independence.py accept   # after reviewing (and patching) any red findings
```

Flags: `--repo <harness checkout>` (default `~/dev/deepseek-harness`), `--home <harness home>` (default `$DSH_HOME` or `~/.dsh`).

## Install as an agent skill

The audit is mechanical; triaging a red run (is this new bundle row egress or local infrastructure?) is judgment. The bundled skill teaches that judgment to your coding agent — [77+ agent platforms](https://github.com/vercel-labs/skills) via the skills CLI:

```sh
npx skills add MisterTK/dsh-independence
```

Then ask your agent things like *"I pulled the new deepseek-harness release — is it still clean?"* The skill runs the audit, triages the diff against documented rules, extends your patch, and re-verifies. The script's exit code stays the gate: the agent can't rationalize past a red run.

deepseek-harness itself discovers filesystem skills, so you can also drop `skills/dsh-independence/` into your dsh project's skills path and have the locked-down harness audit its own upgrades.

## How it works

- **Lockdown patch**: `disabled: true` rows for every egress channel, appended (comment-preserving, idempotent) to `~/.dsh/cordis.patch.yml`. The harness composes this layer over its shipped bundles at every launch, for every profile.
- **Audit baseline**: the script snapshots the harness's egress surface — every shipped bundle row, hardcoded URL, `dsh_*` provider wire-extension field, `DSH_*` launch knob, plus a heuristic suspect-row list — into `~/.dsh/harness-audit/baseline/`. `audit` re-snapshots and unified-diffs; any drift exits 1.
- **Reviewed lockdowns**: `skills/dsh-independence/lockdowns/<release>.yaml` records which upstream release was manually audited, when, and why each row is disabled. An unreviewed release still works but warns loudly.
- **Rename protection**: upstream renames freely pre-1.0; the audit fails if any id your patch disables stops existing, so a rename can't silently re-enable telemetry.

## What still leaves your machine afterward

Exactly two things: model requests to the `baseURL`s you configured, and whatever network access the model exercises through the bash tool inside the harness sandbox (which constrains file writes, not egress). Everything else is local files under `~/.dsh` and your workspace.

## Honest limitations

The audit is a tripwire, not a proof. It diffs compositions, endpoint strings, and wire-extension names — which covers how every egress vector in the current codebase is built (config-mounted rows with declared endpoints). A release that restructures dramatically could move those signals; a big architectural diff is your cue for a fresh manual review, not a quick `accept`. For certainty, verify at the network layer (proxy or `lsof`) that a running harness only contacts your providers.

## Contributing and security

Reviewed per-release lockdowns are the contribution that matters most — see [CONTRIBUTING.md](CONTRIBUTING.md). A false-green audit is a security issue: report it privately per [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
