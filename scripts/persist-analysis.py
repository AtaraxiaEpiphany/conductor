#!/usr/bin/env python3
"""persist-analysis — parse project-analyzer's result block tolerantly, write analysis.json.

Promotes the setup §2.2 step-2 "Parse ---ANALYSIS RESULT--- block" invariant into
code (harness-engineering §4.4 promote-into-code; §7.2 verify-don't-generate):
the orchestrator cannot hand-roll ``json.loads`` on agent output, because weak
models emit trailing commas, code fences, smart quotes, or surrounding prose
that strict JSON rejects — and a failed parse either crashes setup or persists
garbage. This helper repairs those common degradations via
``lib.json_utils.parse_tolerant_json`` and writes the clean tree to
``conductor/.conductor/analysis.json`` atomically.

Usage::

    persist-analysis.py --output conductor/.conductor/analysis.json --result "<raw block>"
    persist-analysis.py --output ... --result-file <path>   # stdin if "-"
    persist-analysis.py --output ... --result-file -        # read from stdin

Exit 0 + OK line (prints the detected project_type + language count) on success.
Exit 1 + remediation message on failure — the orchestrator re-dispatches
project-analyzer ONCE with a tightened "raw JSON, no fences, ASCII quotes"
prompt, then retries (bounded retry; mirrors the per-task retry budget).

The result text is the full subagent output; ``extract_result_block`` isolates
the ``---ANALYSIS RESULT---`` … ``---END ANALYSIS RESULT---`` span first, then
``parse_tolerant_json`` repairs the JSON inside it.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from env import get_plugin_root  # noqa: F401  (grounds plugin root; mirrors sibling scripts)
from json_utils import extract_result_block, parse_tolerant_json, save_json_pretty

_MARKER = "ANALYSIS RESULT"


def _read_result(args):
    if args.result_file:
        if args.result_file == "-":
            return sys.stdin.read()
        return Path(args.result_file).read_text()
    return args.result or ""


def main():
    ap = argparse.ArgumentParser(description="Persist a tolerantly-parsed analysis.json")
    ap.add_argument("--output", default="conductor/.conductor/analysis.json",
                    help="Destination path (default: conductor/.conductor/analysis.json)")
    ap.add_argument("--result", default=None,
                    help="The raw project-analyzer output containing ---ANALYSIS RESULT---")
    ap.add_argument("--result-file", default=None,
                    help="Path to a file holding the raw output ('-' = stdin)")
    args = ap.parse_args()

    raw = _read_result(args)
    if not raw.strip():
        sys.exit(
            "HALT: no analysis result supplied — pass --result or --result-file. "
            "Re-dispatch conductor:project-analyzer with: 'emit STRICT raw JSON "
            "only — no ``` fences, no trailing commas, ASCII double-quotes only.'")

    block = extract_result_block(raw, _MARKER)
    payload = block if block is not None else raw
    data = parse_tolerant_json(payload)
    if data is None or not isinstance(data, dict):
        sys.exit(
            "HALT: ---ANALYSIS RESULT--- block is not parseable JSON even after "
            "tolerant repair (fences/trailing-commas/smart-quotes). Re-dispatch "
            "conductor:project-analyzer with: 'emit STRICT raw JSON only — no "
            "``` fences, no trailing commas, ASCII double-quotes only.'")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    save_json_pretty(out, data)

    ptype = data.get("project_type", "?")
    langs = len(data.get("languages") or [])
    print(f"OK: analysis.json written (project_type={ptype}, languages={langs})")


if __name__ == "__main__":
    main()
