#!/usr/bin/env python3
"""scaffold-strategy — write conductor/workflow/testing/strategy.md from the template.

Promotes the setup §2.4 step-3 invariant into code: read the testing-strategy
template, substitute {TEST_ROOT} with the resolved test root, filter the
language-specific rows/examples/cache-rules to the detected languages, write the
target byte-exact modulo the token + filter, and self-verify. The orchestrator
cannot skip or drift it (harness-engineering §4.4 promote-into-code; §7.2
verify-don't-generate).

Test-root resolution order: --test-root flag, else analysis.json
``structure.test_dirs[0]`` (trailing ``/`` stripped), else ``tests`` (greenfield).

Language filter resolution order: --languages flag (comma-separated), else
analysis.json ``languages[].name`` (normalized via LANG_ALIASES), else ``None``
meaning "no detection → keep all languages" (the greenfield / unknown-stack
fallback; the doc is never smaller than today's worst case).

Template rows/bullets carry trailing ``<!-- lang:<key> [<key>...] -->`` markers.
A tagged line survives iff one of its keys is in the detected set; the marker is
stripped from every kept line so the rendered doc carries no filter markup.
Untagged lines are language-agnostic and always kept.

Exit 0 + OK line on success; exit 1 + remediation message on any failure (§4.3).
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root


# Display name / alias → canonical marker key (the 8 supported languages that the
# setup §2.3 styleguide table covers). Unknown names have no mapping → ignored.
LANG_ALIASES = {
    "python": "python",
    "javascript": "javascript",
    "js": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "go": "go",
    "golang": "go",
    "c++": "cpp",
    "cpp": "cpp",
    "c#": "csharp",
    "csharp": "csharp",
    "java": "java",
    "dart": "dart",
}

# Matches a trailing `<!-- lang:go cpp -->` marker on a line. Capture group is the
# space-separated key list. Anchored to end-of-line (optional trailing whitespace)
# so it only ever strips a genuine trailing filter marker, never mid-prose markup.
_LANG_MARKER = re.compile(r"\s*<!--\s*lang:([a-z+#0-9 ]*?)\s*-->\s*$")

# Marker keys the template is allowed to carry. Lines tagged with anything else are
# a template bug — kept-but-flagged via the keep-all fallback is unsafe (would drop
# the line), so HALT loudly instead of silently mishandling.
_KNOWN_KEYS = {"python", "javascript", "typescript", "go", "cpp", "csharp", "java", "dart"}


def resolve_root(analysis_path, override):
    """analysis.json structure.test_dirs[0] (trailing '/' stripped), else override, else 'tests'."""
    if override:
        return override.rstrip("/") or "tests"
    if analysis_path.exists():
        try:
            data = json.loads(analysis_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            sys.exit(f"HALT: analysis.json unreadable ({e}) -- pass --test-root")
        test_dirs = (data.get("structure", {}) or {}).get("test_dirs") or []
        if test_dirs and test_dirs[0]:
            return str(test_dirs[0]).rstrip("/")
    return "tests"


def detect_languages(analysis_path):
    """analysis.json languages[].name → set of canonical keys, or None if unknown.

    ``None`` is the "no detection" sentinel → caller keeps all languages. Triggered
    by a missing analysis.json, a missing/empty ``languages`` list, or a list whose
    names are all unrecognized (e.g. [{name:"Rust"}]). Never raises — greenfield and
    exotic stacks fall back to the full doc rather than blocking setup.
    """
    if not analysis_path.exists():
        return None
    try:
        data = json.loads(analysis_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    names = (data.get("languages") or [])
    keys = set()
    for entry in names:
        name = ""
        if isinstance(entry, dict):
            name = str(entry.get("name") or "")
        elif isinstance(entry, str):
            name = entry
        key = LANG_ALIASES.get(name.strip().lower())
        if key:
            keys.add(key)
    return keys or None


def filter_by_language(text, langs):
    """Drop template lines whose ``<!-- lang:... -->`` marker misses ``langs``.

    ``langs is None`` → keep every line (no-detection fallback), but still strip
    any trailing markers so the rendered doc is clean. Otherwise keep a tagged
    line iff its key set intersects ``langs``; untagged lines always survive. The
    marker is stripped from every kept line.
    """
    out = []
    for line in text.splitlines(keepends=True):
        body, newline = (line[:-1], line[-1]) if line.endswith("\n") else (line, "")
        m = _LANG_MARKER.search(body)
        if not m:
            out.append(line)
            continue
        keys = {k for k in m.group(1).split() if k}
        unknown = keys - _KNOWN_KEYS
        if unknown:
            sys.exit(f"HALT: unknown lang marker key(s) {sorted(unknown)!r} in template line: {body!r}")
        keep = langs is None or bool(keys & langs)
        if keep:
            # Strip the marker (and the trailing whitespace it absorbed) from the body.
            body = body[:m.start()].rstrip(" \t")
            out.append(body + newline)
    return "".join(out)


def _fmt_langs(langs):
    return "all (no detection)" if langs is None else ",".join(sorted(langs))


def main():
    plugin_root = get_plugin_root()
    ap = argparse.ArgumentParser(description="Scaffold testing/strategy.md from the template.")
    ap.add_argument("--template", default=str(plugin_root / "templates" / "testing" / "strategy.md"))
    ap.add_argument("--out", default="conductor/workflow/testing/strategy.md")
    ap.add_argument("--analysis", default="conductor/.conductor/analysis.json")
    ap.add_argument("--test-root", default=None,
                    help="Override; else analysis.json structure.test_dirs[0], else 'tests'")
    ap.add_argument("--languages", default=None,
                    help="Override; comma-separated (e.g. python,typescript). "
                         "Else analysis.json languages[].name, else keep all.")
    args = ap.parse_args()

    template = Path(args.template)
    if not template.exists():
        sys.exit(f"HALT: template missing: {template} (is CLAUDE_PLUGIN_ROOT set correctly?)")
    root = resolve_root(Path(args.analysis), args.test_root)
    langs = _parse_languages(args.languages) if args.languages else detect_languages(Path(args.analysis))

    try:
        text = template.read_text()
    except OSError as e:
        sys.exit(f"HALT: cannot read template {template}: {e}")

    text = text.replace("{TEST_ROOT}", root)
    text = filter_by_language(text, langs)

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(text)
    except OSError as e:
        sys.exit(f"HALT: cannot write {target}: {e} -- check path/permissions")

    # Self-verify (§7.1 L0): the token must be gone in the written file.
    if "{TEST_ROOT}" in target.read_text():
        sys.exit(f"HALT: {{TEST_ROOT}} still present in {target} after substitution")
    # Self-verify: no filter markup may remain in the rendered doc.
    if "<!-- lang:" in target.read_text():
        sys.exit(f"HALT: <!-- lang: --> marker survived in {target} (filter bug)")
    print(f"OK: strategy.md root={root} langs={_fmt_langs(langs)} -> {target}")


def _parse_languages(spec):
    """Comma-separated CLI string → set of canonical keys. Unknown terms HALT."""
    keys = set()
    for term in spec.split(","):
        term = term.strip().lower()
        if not term:
            continue
        key = LANG_ALIASES.get(term)
        if not key:
            sys.exit(f"HALT: unknown --languages term {term!r}; known: {sorted(LANG_ALIASES)}")
        keys.add(key)
    return keys or None


if __name__ == "__main__":
    main()

