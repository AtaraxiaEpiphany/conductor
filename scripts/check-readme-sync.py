#!/usr/bin/env python3
"""check-readme-sync — README drift gate (generate + verify volatile fragments).

The README's volatile facts are DERIVED from their single homes, never
hand-maintained:

  * the **agent table** ← ``agents/*.md`` frontmatter (name / model /
    description),
  * the **command table** ← ``skills/*/SKILL.md`` frontmatter (name /
    description — the same text Claude Code shows in the skill picker),
  * the **CLI group table + count** ← ``track_state/commands.py``
    (COMMAND_GROUPS — the same single source the pre-command guard derives
    from; file-loaded to skip the package chain),
  * the **inventory counts** (agents / skills / hooks in the Features bullet
    and the architecture tree) ← directory listings + ``hooks/hooks.json``.

Fragments between ``<!-- conductor:begin:<name> -->`` / ``<!-- conductor:end:<name> -->``
markers are REGENERATED verbatim; counts embedded in hand-written sentences are
verified by regex (and re-written with ``--fix``). Everything outside markers is
hand prose and never touched.

Default mode verifies and exits 1 with a drift report; ``--fix`` rewrites
README.md in place. The pre-1.1 README drifted four times in one campaign
(23-vs-24 agents, a missing build-runner row, 16-vs-17 skills) — this gate makes
that class of rot a CI failure, not a review-luck discovery.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

FRAGMENT_RE = r"(<!-- conductor:begin:{name} -->\n)(.*?)(\n<!-- conductor:end:{name} -->)"


def get_root():
    return ROOT


# --- source readers -----------------------------------------------------------

def _frontmatter(path):
    """Parse the leading ``---`` frontmatter block into a dict (first-win)."""
    fm = {}
    if not path.exists():
        return fm
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return fm
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            fm.setdefault(m.group(1), m.group(2).strip())
    return fm


def agent_rows(root):
    """(name, model, description) per agents/*.md, sorted by name."""
    rows = []
    for path in sorted((root / "agents").glob("*.md")):
        fm = _frontmatter(path)
        if "name" not in fm:
            continue
        rows.append((fm["name"], fm.get("model", "sonnet"),
                     fm.get("description", "")))
    return rows


def skill_rows(root):
    """(name, description) per skills/*/SKILL.md, sorted by name."""
    rows = []
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        fm = _frontmatter(path)
        if "name" not in fm:
            continue
        rows.append((fm["name"], fm.get("description", "")))
    return rows


def command_groups():
    """COMMAND_GROUPS from track_state/commands.py, file-loaded.

    Direct file-load (not ``import track_state.commands``) so this lint — and
    any future hook consumer — never pays the track_state package-import chain.
    commands.py is a stdlib-only leaf by contract (see its docstring).
    """
    path = ROOT / "scripts" / "track_state" / "commands.py"
    spec = importlib.util.spec_from_file_location("track_state_commands_readme", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.COMMAND_GROUPS


def hook_counts(root):
    """(event_types, entries) from hooks/hooks.json."""
    data = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    events = data.get("hooks", data)
    if isinstance(events, dict):
        return len(events), sum(len(v) for v in events.values())
    return 0, 0


# --- fragment renderers -------------------------------------------------------

def render_agents_table(root):
    lines = ["| Agent | Model | Purpose |", "|-------|-------|---------|"]
    for name, model, desc in agent_rows(root):
        lines.append(f"| `{name}` | {model} | {desc} |")
    return "\n".join(lines)


def render_commands_table(root):
    lines = ["| Command | Description |", "|---------|-------------|"]
    for name, desc in skill_rows(root):
        lines.append(f"| `/conductor:{name}` | {desc} |")
    return "\n".join(lines)


def render_cli_groups():
    groups = command_groups()
    flat = [c for _g, cmds in groups for c in cmds]
    lines = [
        f"The `bin/track-state` command provides direct state management. Run "
        f"`bin/track-state help` for the full, current list ({len(flat)} "
        f"subcommands across {len(groups)} groups) — it is grouped and "
        f"self-describing. The complete surface, straight from "
        f"`track_state/commands.py` (the same single source the pre-command "
        f"guard derives its sanctioned set from):",
        "",
        "| Group | Subcommands |",
        "|-------|-------------|",
    ]
    for gname, cmds in groups:
        inline = ", ".join(f"`{c}`" for c in cmds)
        lines.append(f"| **{gname}** | {inline} |")
    return "\n".join(lines)


FRAGMENTS = {
    "agents-table": lambda root: render_agents_table(root),
    "commands-table": lambda root: render_commands_table(root),
    "cli-groups": lambda root: render_cli_groups(),
}


# --- count verification (hand sentences; regex-verified, regex-fixed) ---------

def _count_fixers(root):
    """(pattern, replacement_fn, human description) per embedded count."""
    n_agents = len(agent_rows(root))
    n_skills = len(skill_rows(root))
    n_events, n_entries = hook_counts(root)
    return [
        (r"(dispatches )\d+( specialized AI agents)",
         lambda m: f"{m.group(1)}{n_agents}{m.group(2)}",
         f"Features bullet agent count → {n_agents}"),
        (r"(agents/\s+)\d+(\s+specialised agent definitions)",
         lambda m: f"{m.group(1)}{n_agents}{m.group(2)}",
         f"tree agents/ count → {n_agents}"),
        (r"(skills/\s+)\d+(\s+slash-command skills)",
         lambda m: f"{m.group(1)}{n_skills}{m.group(2)}",
         f"tree skills/ count → {n_skills}"),
        (r"(hooks/hooks\.json\s+)\d+(\s+hook event types,\s+)\d+(\s+hook entries)",
         lambda m: f"{m.group(1)}{n_events}{m.group(2)}{n_entries}{m.group(3)}",
         f"tree hooks counts → {n_events} event types, {n_entries} entries"),
    ]


# --- README surgery -----------------------------------------------------------

def replace_fragment(text, name, body):
    """Swap the marked fragment; raise KeyError with a clear message if absent."""
    pattern = FRAGMENT_RE.format(name=re.escape(name))
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        raise KeyError(
            f"README.md is missing the <!-- conductor:begin:{name} --> / "
            f"<!-- conductor:end:{name} --> marker pair")
    return text[:m.start()] + f"<!-- conductor:begin:{name} -->\n{body}\n<!-- conductor:end:{name} -->" + text[m.end():]


def sync(readme_text, root):
    """Apply every fragment + count fix. Returns (new_text, drift_descriptions)."""
    text = readme_text
    drift = []

    for name, render in FRAGMENTS.items():
        body = render(root)
        pattern = FRAGMENT_RE.format(name=re.escape(name))
        m = re.search(pattern, text, re.DOTALL)
        if m is None:
            drift.append(f"missing marker pair for fragment '{name}'")
            continue
        current = m.group(2)
        if current != body:
            drift.append(f"fragment '{name}' is stale")
            text = replace_fragment(text, name, body)

    for pattern, fixer, desc in _count_fixers(root):
        text, n = re.subn(pattern, fixer, text, count=1)
        if n == 0:
            drift.append(f"count sentence not found: {desc}")

    # Drift = anything actually changed vs the markers/counts being satisfiable.
    return text, drift, readme_text != text


def main(argv):
    fix = "--fix" in argv
    text = README.read_text(encoding="utf-8")
    new_text, drift, changed = sync(text, get_root())

    # Report REAL drift: re-run verify on the post-fix text; anything still
    # failing (missing markers, unfound sentences) is unfixable-by-design.
    _, residual, _ = sync(new_text, get_root())
    problems = residual + ([f"stale (use --fix): {d}" for d in drift
                            if not d.startswith("missing")
                            and not d.startswith("count sentence")] if not fix else [])

    if fix and changed:
        README.write_text(new_text, encoding="utf-8")
        print(f"check-readme-sync: rewrote README.md ({len(drift)} fragment(s) refreshed)")
    if problems:
        print("check-readme-sync: DRIFT", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("check-readme-sync: OK (fragments + counts match their sources)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
