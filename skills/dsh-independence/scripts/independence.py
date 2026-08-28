# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6"]
# ///
"""dsh-independence: run deepseek-harness with zero egress except your own LLM providers.

Three subcommands, meant to be run with uv (https://docs.astral.sh/uv/):

  uv run independence.py init     write the lockdown patch + record the audit baseline
  uv run independence.py audit    diff the harness's egress surface against the baseline
  uv run independence.py accept   bless the current surface as the new baseline

The lockdown patch is a layer of `disabled: true` rows composed over the shipped
bundles at every harness launch, so it survives upgrades by itself. What it cannot
do is know about egress a NEW release adds — that is what `audit` covers: it
snapshots every shipped bundle row, hardcoded URL, provider wire-extension field,
and launch-time env knob, and exits non-zero when any of it drifts from the
baseline you last accepted. Review the additions, extend the patch if something
phones home, then `accept`.

State lives under $DSH_HOME (default ~/.dsh): the patch at cordis.patch.yml and
the baseline under harness-audit/baseline/. The reviewed per-release lockdown data
ships in ../lockdowns/*.yaml next to this script; a built-in fallback covers
running this file standalone (e.g. `uv run <raw URL>`).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from datetime import date
from pathlib import Path

import yaml

TOOL_VERSION = "0.1.0"

# Fallback lockdown so the script works standalone, without the skill directory.
# A matching lockdowns/<release>.yaml overrides this.
LOCKDOWN_FALLBACK: dict = {
    "release": "0.1.2-alpha.1",
    "reviewed": "2026-08-28",
    "disable": [
        {"id": "session-telemetry-otel",
         "reason": "uploads raw session records to harness-telemetry.deepseeksvc.com (FEEDBACK_ONLY by default)"},
        {"id": "llm-deepseek",
         "reason": "sends x-deepseek-harness-user-id / -session-id headers on every request"},
        {"id": "plugin-package-inventory-deepseek",
         "reason": "dsh_plugin_packages body field: active plugin inventory in every LLM request, on by default"},
        {"id": "session-log-deepseek",
         "reason": "dsh_session_log body field: full session log inside LLM requests (off by default upstream; belt and braces)"},
        {"id": "web-search-deepseek",
         "reason": "web_search queries go to DeepSeek's Messages endpoint"},
        {"id": "tool-web",
         "reason": "model-facing web tools; disabled rather than left pointing at a dead search seam"},
    ],
}

SNAPSHOT_FILES = (
    "bundle-rows.txt",
    "bundles.txt",
    "urls.txt",
    "wire-extensions.txt",
    "env-knobs.txt",
    "suspect-rows.txt",
)

URL_RE = re.compile(r"https?://[A-Za-z0-9._:/?#@!$&*+,;=%~-]+")
BENIGN_URL_RE = re.compile(
    r"github\.com|npmjs\.(com|org)|example\.(com|org|net)|localhost|127\.0\.0\.1"
    r"|\[::1\]|w3\.org|opensource\.org|registry\.npm|json-schema\.org|yaml\.org",
    re.I,
)
WIRE_EXT_RE = re.compile(r"""['"](dsh_[a-z_]+)['"]""")
ENV_KNOB_RE = re.compile(r"DSH_[A-Z_]+")
# Rows worth a second look by name alone: telemetry-ish vocabulary plus anything
# DeepSeek-branded, since those are exactly the rows a lockdown exists to review.
SUSPECT_NAME_RE = re.compile(r"telemetry|otel|analytics|metric|tracking|inventory|deepseek", re.I)


class TolerantLoader(yaml.SafeLoader):
    """SafeLoader that accepts harness-specific tags (e.g. `!!js`) as plain values.

    Still safe: it extends SafeLoader (no python/object construction), and the
    catch-all constructor below only ever builds plain scalars, lists, and dicts —
    a `!!js` expression comes back as its literal source string, never evaluated.
    """


def _tolerant_construct(loader: TolerantLoader, _suffix: str, node: yaml.Node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


TolerantLoader.add_multi_constructor("", _tolerant_construct)


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=TolerantLoader)


def fail(message: str) -> "sys.NoReturn":
    print(f"independence: {message}", file=sys.stderr)
    raise SystemExit(2)


# ── harness surface snapshot ──────────────────────────────────────────────────

def bundle_patch_files(repo: Path) -> list[Path]:
    return sorted((repo / "packages" / "bundle").glob("*/cordis.patch.yml"))


def iter_rows(entries) -> list[dict]:
    """Flatten patch entries: `insert` lists and id-targeted rows both yield rows."""
    rows: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("insert"), list):
            rows.extend(row for row in entry["insert"] if isinstance(row, dict))
        if "id" in entry or "name" in entry:
            rows.append(entry)
    return rows


def shipped_rows(repo: Path) -> list[dict]:
    rows: list[dict] = []
    for file in bundle_patch_files(repo):
        rows.extend(iter_rows(load_yaml(file)))
    return rows


def strings_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return []


def source_files(repo: Path) -> list[Path]:
    """Package source (src/ trees) — where default endpoints and wire fields live."""
    return [
        path
        for path in (repo / "packages").rglob("*")
        if path.suffix in {".ts", ".tsx"} and "/src/" in path.as_posix()
    ]


def scan_urls(text: str) -> set[str]:
    found = set()
    for match in URL_RE.findall(text):
        url = match.rstrip("'\").,")
        if url and not BENIGN_URL_RE.search(url):
            found.add(url)
    return found


def snapshot(repo: Path) -> dict[str, list[str]]:
    """The egress-relevant surface of one harness checkout, as sorted text lines."""
    bundle_dir = repo / "packages" / "bundle"
    if not bundle_dir.is_dir():
        fail(f"not a deepseek-harness checkout: {repo}")

    rows = shipped_rows(repo)
    row_lines = sorted({
        f"id={row.get('id', '-')} name={row.get('name', '-')}" for row in rows
    })

    urls: set[str] = set()
    knobs: set[str] = set()
    for file in bundle_patch_files(repo):
        text = file.read_text(encoding="utf-8")
        urls |= scan_urls(text)
        knobs |= set(ENV_KNOB_RE.findall(text))

    wire: set[str] = set()
    for file in source_files(repo):
        text = file.read_text(encoding="utf-8", errors="replace")
        urls |= scan_urls(text)
        wire |= set(WIRE_EXT_RE.findall(text))

    cli_src = repo / "apps" / "cli" / "src"
    if cli_src.is_dir():
        for file in cli_src.rglob("*.ts"):
            knobs |= set(ENV_KNOB_RE.findall(file.read_text(encoding="utf-8", errors="replace")))

    # Suspect rows: flagged by name vocabulary or by carrying a non-benign URL in
    # config. Deliberately independent of the user's patch — the point is that a
    # NEWLY suspect row diffs red even when no lockdown knows about it yet.
    suspects: set[str] = set()
    for row in rows:
        ident = f"id={row.get('id', '-')} name={row.get('name', '-')}"
        reasons = []
        # Strip the npm scope before matching: every harness package lives under
        # @deepseek-ai/, so the raw name would flag all rows as "deepseek".
        bare_name = str(row.get("name", "")).removeprefix("@deepseek-ai/")
        if SUSPECT_NAME_RE.search(f"{row.get('id', '')} {bare_name}"):
            reasons.append("name")
        config_urls = sorted({u for s in strings_in(row.get("config")) for u in scan_urls(s)})
        reasons.extend(f"url:{u}" for u in config_urls)
        if reasons:
            suspects.add(f"{ident} [{', '.join(reasons)}]")

    return {
        "bundle-rows.txt": row_lines,
        "bundles.txt": sorted(p.name for p in bundle_dir.iterdir() if p.is_dir()),
        "urls.txt": sorted(urls),
        "wire-extensions.txt": sorted(wire),
        "env-knobs.txt": sorted(knobs),
        "suspect-rows.txt": sorted(suspects),
    }


# ── lockdown data ─────────────────────────────────────────────────────────────

def harness_release(repo: Path) -> str:
    manifest = repo / "package.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "unknown"))
    except OSError:
        return "unknown"


def load_lockdown(release: str) -> tuple[dict, list[str]]:
    """The reviewed lockdown for this release, plus any warnings about the match."""
    warnings: list[str] = []
    lockdown_dir = Path(__file__).resolve().parent.parent / "lockdowns"
    candidates = sorted(lockdown_dir.glob("*.yaml")) if lockdown_dir.is_dir() else []
    exact = next((p for p in candidates if p.stem == release), None)
    if exact is not None:
        return load_yaml(exact), warnings
    chosen = (load_yaml(candidates[-1]), candidates[-1].stem) if candidates \
        else (LOCKDOWN_FALLBACK, LOCKDOWN_FALLBACK["release"])
    if release != chosen[1]:
        warnings.append(
            f"harness release {release} has no reviewed lockdown; using the one for "
            f"{chosen[1]} — treat every audit diff as unreviewed territory"
        )
    return chosen[0], warnings


# ── the user's patch layer ────────────────────────────────────────────────────

def patch_path(home: Path) -> Path:
    return home / "cordis.patch.yml"


def disabled_ids(patch_file: Path) -> set[str]:
    if not patch_file.is_file():
        return set()
    entries = load_yaml(patch_file) or []
    if not isinstance(entries, list):
        fail(f"{patch_file} is not a top-level YAML list; refusing to touch it")
    return {
        str(entry["id"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("disabled") is True and "id" in entry
    }


PATCH_HEADER = """\
# deepseek-harness egress lockdown — managed additions by dsh-independence.
# This home patch layer is composed over the shipped bundles at every launch,
# for every profile, and survives upgrades. Run the audit after each upgrade:
#   uv run independence.py audit
#
# Add your own provider routes below (field reference: the harness's
# packages/llm/llm-pi-ai/README.md), e.g.:
#
# - id: llm-pi-ai
#   config:
#     providers:
#       my-gateway:
#         api: openai-completions
#         baseURL: https://llm.internal.example/v1
#         apiKeyEnv: MY_GATEWAY_API_KEY
#         models:
#           - id: my-model
#             contextWindow: 262144
# - id: agent-default-model
#   config:
#     provider: my-gateway
#     model: my-model
"""


def ensure_patch(home: Path, lockdown: dict) -> list[str]:
    """Append any missing lockdown disables to the patch, preserving user content.

    Textual append, not a YAML rewrite: the patch is user-owned and full of
    comments a round-trip would destroy. Appended items extend the top-level
    list, which stays valid YAML. Returns the row ids that were added.
    """
    file = patch_path(home)
    present = disabled_ids(file)
    missing = [row for row in lockdown["disable"] if row["id"] not in present]
    if not missing:
        return []
    block = "".join(
        f"\n# {row['reason']}\n- id: {row['id']}\n  disabled: true\n" for row in missing
    )
    stamp = (
        f"\n# ── dsh-independence lockdown (release {lockdown['release']}, "
        f"applied {date.today().isoformat()}) ──\n"
    )
    if file.is_file():
        file.write_text(file.read_text(encoding="utf-8").rstrip("\n") + "\n" + stamp + block,
                        encoding="utf-8")
    else:
        home.mkdir(parents=True, exist_ok=True)
        file.write_text(PATCH_HEADER + stamp + block, encoding="utf-8")
    still_missing = {row["id"] for row in lockdown["disable"]} - disabled_ids(file)
    if still_missing:
        fail(f"patch write did not take for rows: {', '.join(sorted(still_missing))}")
    return [row["id"] for row in missing]


# ── baseline ──────────────────────────────────────────────────────────────────

def baseline_dir(home: Path) -> Path:
    return home / "harness-audit" / "baseline"


def write_baseline(home: Path, surface: dict[str, list[str]], release: str) -> None:
    base = baseline_dir(home)
    base.mkdir(parents=True, exist_ok=True)
    for name in SNAPSHOT_FILES:
        (base / name).write_text("\n".join(surface[name]) + "\n", encoding="utf-8")
    (base / "meta.json").write_text(
        json.dumps(
            {"release": release, "recorded": date.today().isoformat(), "tool": TOOL_VERSION},
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def read_baseline(home: Path) -> dict[str, list[str]] | None:
    base = baseline_dir(home)
    if not base.is_dir():
        return None
    result = {}
    for name in SNAPSHOT_FILES:
        file = base / name
        result[name] = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
    return result


# ── commands ──────────────────────────────────────────────────────────────────

def cmd_init(repo: Path, home: Path) -> int:
    release = harness_release(repo)
    lockdown, warnings = load_lockdown(release)
    for warning in warnings:
        print(f"WARNING: {warning}")
    added = ensure_patch(home, lockdown)
    if added:
        print(f"Patch {patch_path(home)}: added disables for {', '.join(added)}")
    else:
        print(f"Patch {patch_path(home)}: all {len(lockdown['disable'])} lockdown rows already disabled")
    surface = snapshot(repo)
    if read_baseline(home) is None:
        write_baseline(home, surface, release)
        print(f"Baseline recorded at {baseline_dir(home)} (release {release}).")
        print("Review the baseline files once by hand; future audits diff against them.")
    else:
        print("Baseline already exists; leaving it (run `audit`, or `accept` to re-record).")
    return 1 if warnings else 0


def cmd_audit(repo: Path, home: Path) -> int:
    release = harness_release(repo)
    lockdown, warnings = load_lockdown(release)
    surface = snapshot(repo)
    status = 0

    for warning in warnings:
        print(f"WARNING: {warning}")
        status = 1

    # Coverage both ways: every lockdown row must still be disabled in the patch,
    # and every patched id must still exist upstream (rename/split detection).
    patched = disabled_ids(patch_path(home))
    required = {row["id"] for row in lockdown["disable"]}
    for row_id in sorted(required - patched):
        print(f"FAIL: lockdown row '{row_id}' is not disabled in {patch_path(home)} — run `init`")
        status = 1
    shipped_ids = {line.split(" name=")[0].removeprefix("id=") for line in surface["bundle-rows.txt"]}
    for row_id in sorted(patched - shipped_ids):
        print(f"WARNING: patched row id '{row_id}' no longer exists upstream (renamed? split?) — re-audit it")
        status = 1

    baseline = read_baseline(home)
    if baseline is None:
        print(f"No baseline at {baseline_dir(home)} — run `init` first.")
        return 2

    for name in SNAPSHOT_FILES:
        diff = list(difflib.unified_diff(
            baseline[name], surface[name],
            fromfile=f"baseline/{name}", tofile=f"current/{name}", lineterm="",
        ))
        if diff:
            print(f"\n== CHANGED: {name} (+ lines are new since your accepted baseline) ==")
            print("\n".join(diff))
            status = 1

    if status == 0:
        print(f"OK: egress surface unchanged since accepted baseline (release {release}).")
    else:
        print(
            "\nReview the findings above. Disable new egress rows in "
            f"{patch_path(home)}, then run `accept` to bless the new state."
        )
    return status


def cmd_accept(repo: Path, home: Path) -> int:
    release = harness_release(repo)
    write_baseline(home, snapshot(repo), release)
    print(f"Baseline updated at {baseline_dir(home)} (release {release}).")
    print(f"Make sure anything new that phones home is disabled in {patch_path(home)}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="independence.py",
        description="Egress lockdown and per-upgrade audit for deepseek-harness.",
    )
    parser.add_argument("command", choices=["init", "audit", "accept"])
    parser.add_argument(
        "--repo", type=Path, default=Path.home() / "dev" / "deepseek-harness",
        help="deepseek-harness checkout (default: ~/dev/deepseek-harness)",
    )
    parser.add_argument(
        "--home", type=Path, default=None,
        help="harness home (default: $DSH_HOME or ~/.dsh)",
    )
    args = parser.parse_args(argv)
    import os
    home = args.home or Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh")
    repo = args.repo.expanduser().resolve()
    command = {"init": cmd_init, "audit": cmd_audit, "accept": cmd_accept}[args.command]
    return command(repo, home.expanduser())


if __name__ == "__main__":
    raise SystemExit(main())
