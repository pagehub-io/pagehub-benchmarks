#!/usr/bin/env python3
"""Run exactly the given test IDs and report a structured result.

IDs come from a JSON file and are passed to pytest as argv, never through a
shell: some parametrized ids contain spaces (e.g. "test_tofile[home_is_data,
home_is_config]") and any space-joined form silently splits them, which makes
pytest collect nothing and report "no tests ran" — an outcome that reads as
success to a naive grep for "failed".

Asserts that the number of tests that actually ran equals the number requested.
"""
import json, re, subprocess, sys

ids = json.load(open(sys.argv[1]))
if not ids:
    print(json.dumps({"requested": 0, "ran": 0, "passed": 0, "failed": 0, "ok": True}))
    sys.exit(0)

cmd = [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
       "-W", "ignore::astropy.utils.exceptions.AstropyWarning", *ids]
p = subprocess.run(cmd, capture_output=True, text=True)
out = p.stdout + p.stderr
tail = out.strip().splitlines()[-1] if out.strip() else ""

def n(pat):
    m = re.search(pat, out)
    return int(m.group(1)) if m else 0

passed, failed, errors = n(r"(\d+) passed"), n(r"(\d+) failed"), n(r"(\d+) error")
ran = passed + failed + errors
print(json.dumps({
    "requested": len(ids), "ran": ran, "passed": passed, "failed": failed,
    "errors": errors, "ok": ran == len(ids) and failed == 0 and errors == 0,
    "collected_all": ran == len(ids), "tail": tail[:160],
}))
