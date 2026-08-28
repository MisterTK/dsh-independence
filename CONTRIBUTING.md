# Contributing

Thanks for helping people run deepseek-harness on their own terms.

## The contribution that matters most: reviewed lockdowns

Upstream is pre-1.0 and changes fast. Each `skills/dsh-independence/lockdowns/<release>.yaml` records one manually audited upstream release: which bundle rows egress, why each is disabled, and when it was reviewed. When a new deepseek-harness release lands:

1. Run `uv run skills/dsh-independence/scripts/independence.py audit --repo <checkout>` against the new tree.
2. Triage every `+` line using the rules in [SKILL.md](skills/dsh-independence/SKILL.md) — read the owning package's source; do not judge rows by name.
3. Copy the newest lockdown yaml to `<new-release>.yaml`, update `release`, `reviewed`, `notes`, and the `disable` list, citing the file/line that proves each new disable.
4. Open a PR with the evidence in the description.

## Code changes

- `scripts/independence.py` is a single PEP 723 file on purpose — it must keep working standalone via `uv run <raw URL>` with no repo around it. Keep the embedded `LOCKDOWN_FALLBACK` in sync with the newest lockdown yaml.
- Run the smoke test before pushing: `bash tests/smoke.sh` (CI runs it on Linux and macOS).
- Keep the scope honest: this tool controls *egress*. Features that don't affect what leaves a user's machine belong elsewhere.

## Reporting problems

A false-green audit (real egress the audit missed) is a security issue — see [SECURITY.md](SECURITY.md). Everything else: open an issue with the audit output and your harness release.
