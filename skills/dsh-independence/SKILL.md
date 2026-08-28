---
name: dsh-independence
description: Lock down deepseek-harness so no data leaves the machine except LLM calls to the user's own providers, and audit each harness upgrade for new egress. Use whenever the user mentions deepseek-harness (or dsh) together with privacy, telemetry, tracking, egress, phoning home, self-hosting, "no ties to DeepSeek", using their own/local models with the harness, or upgrading/pulling a new harness release with a lockdown in place — even if they only say something like "I updated the harness, is it still clean?"
---

# dsh-independence

Run [deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) with zero egress except LLM calls to providers the user chose. Two parts, split by nature:

- **`scripts/independence.py`** is the mechanical gate. It writes the lockdown patch, snapshots the harness's egress surface, and diffs it against an accepted baseline. Its exit code is the verdict — do not overrule a red run by reasoning; a nonzero exit means a human-reviewable finding exists.
- **This skill is the judgment layer**: how to triage what the script flags. The script cannot decide whether a new bundle row phones home; you can, using the triage rules below.

Prerequisite: `uv` (the script is a PEP 723 single file; `uv run` resolves its one dependency, pyyaml, at execution time — nothing is installed).

## Commands

All commands accept `--repo <harness checkout>` (default `~/dev/deepseek-harness`) and `--home <harness home>` (default `$DSH_HOME` or `~/.dsh`).

```sh
uv run {baseDir}/scripts/independence.py init    # first-time setup: patch + baseline
uv run {baseDir}/scripts/independence.py audit   # after every harness upgrade
uv run {baseDir}/scripts/independence.py accept  # bless reviewed changes as the new baseline
```

`init` is idempotent and comment-preserving: it appends only missing `disabled: true` rows to `~/.dsh/cordis.patch.yml` (the harness's home patch layer, composed over shipped bundles at every launch) and records the baseline only if absent. The user's own rows — provider routes, custom config — are never rewritten.

## First-time setup

1. Run `init`. If it warns the harness release has no reviewed lockdown, tell the user plainly: the disables still apply, but the release has not been manually audited, so run an `audit` and triage everything it shows.
2. The patch ships with a commented `llm-pi-ai` provider template. Help the user fill in their own routes (a route named after a pi-ai catalog provider needs only `apiKeyEnv`; a self-hosted gateway declares `api` + `baseURL` + `models`) and set `agent-default-model` to match. Field reference: the harness's `packages/llm/llm-pi-ai/README.md`.
3. Check environment hygiene: no `DEEPSEEK_API_KEY` in env, `.env`, or `~/.dsh/.credentials.yaml`. Optionally delete `~/.dsh/.anonymous-user-id` (a random UUID; with the lockdown in place it never leaves the machine anyway).

## After a harness upgrade

Run `audit`. Green (exit 0) means the egress surface is byte-identical to what was last reviewed — report that and stop. Red means one or more findings; triage each `+` line with the rules below, extend the patch where needed, then run `accept` and re-run `audit` to confirm green.

## Triage rules for a red audit

The audit diffs six files. What each means and what to do:

**`bundle-rows.txt` / `bundles.txt` — a new shipped plugin row or bundle.** Decide whether the row can emit data off-machine. Read its package README or source in the harness checkout; do not judge by name alone. The distinction that matters: rows that only touch local state (storage, projection, session persistence, UI, sandboxing) are fine; rows that construct any network client, register a provider LLM request extension, or carry an endpoint in config are egress. For egress rows, append to the user's patch:

```yaml
# <why it phones home>
- id: <row-id>
  disabled: true
```

Disabling the row is the only reliable lever — a patch replaces a row's whole `config`, and config cannot disable a row, so never try to neutralize an egress row by overriding its URL to something inert.

**`urls.txt` — a new hardcoded URL.** Locate it (`grep -rn <url>` in the harness checkout). A URL in docs, tests, or error messages is noise; a URL in a bundle `cordis.patch.yml` or a `src/` default is a default endpoint — find its owning row and disable it.

**`wire-extensions.txt` — a new `dsh_*` field.** These are body fields injected into provider LLM requests via the harness's `DeepSeekLlmApiExtensionRegistry`. Find the contributing plugin (search the field name in `packages/*/src`) and disable its row. Check whether it is enabled by default upstream — that decides urgency, not whether to disable it.

**`env-knobs.txt` — a new `DSH_*` switch.** Check its default in the bundle or launcher source. A knob that defaults to sharing (like `DSH_TELEMETRY_MODE`'s `FEEDBACK_ONLY` default) needs its row disabled; a knob that defaults off just gets mentioned to the user.

**`suspect-rows.txt` — heuristic flags** (telemetry-ish names, URLs in config). Every line here also appears in `bundle-rows.txt` or reflects a config change; it is a prioritization hint, not an independent finding. Triage the suspect lines first.

**Coverage warnings** (`patched row id '<x>' no longer exists upstream`): upstream renamed or split a row the patch disables. Find the successor (search the old plugin package name in `packages/bundle/*/cordis.patch.yml`), disable the new id, and only then remove the stale one. This warning is the mechanism that stops a rename from silently re-enabling telemetry — never just delete the stale row to silence it.

**`WARNING: release has no reviewed lockdown`**: the harness version is newer than this skill's newest `lockdowns/*.yaml`. Everything still works, but treat the whole diff as unreviewed. If the triage results in patch changes, offer to write a new `lockdowns/<release>.yaml` (copy the newest one, update `release`/`reviewed`/`disable`) so the review is captured for others.

## What NOT to disable

The lockdown's scope is "nothing leaves the machine except the user's own LLM calls." Keep that boundary in both directions:

- `llm-pi-ai` and `agent-default-model` are how the user's own providers run — never disable them.
- Local-only infrastructure (session persistence, storage, sandbox, subprocess, skills, commands, compaction, subagents) stays enabled; disabling it breaks the harness without reducing egress.
- `web-fetch-http` is anonymous direct HTTP the *model* can invoke; whether that counts as acceptable egress is the user's call, not a default. Ask if it comes up. (The shipped default has `tool-web` disabled by this lockdown, so it is moot until the user re-enables web tools.)
- `command-feedback` is log-only once telemetry is disabled; leaving it costs nothing.

## Verify

After any patch change: run `audit` and require exit 0. Additionally, the strongest end-to-end check when the user wants certainty: launch the harness with their profile and confirm from a network vantage point (e.g. `lsof -i` scoping, or a proxy) that the only outbound connections go to their configured provider `baseURL`s.
