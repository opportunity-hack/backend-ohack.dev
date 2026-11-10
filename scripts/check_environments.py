#!/usr/bin/env python3
"""Compare each backend environment's GET /api/health against
docs/environments.expected.json. Exit 1 on drift or an unreachable env.

Stdlib only (runs in a bare GitHub Actions python). Samples each endpoint
several times and reports the distinct worker_pid / machine_id seen, which
shows how many independent in-process cache copies answer requests.

    python scripts/check_environments.py [--samples 10] [--expected PATH]
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_EXPECTED = os.path.join(HERE, "..", "docs", "environments.expected.json")


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "ohack-env-drift-check"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check(name, spec, samples):
    problems, bodies = [], []
    for i in range(samples):
        try:
            bodies.append(fetch(spec["url"]))
        except Exception as exc:  # noqa: BLE001 - report every failure kind
            problems.append(f"{name}: request {i + 1} to {spec['url']} failed: {exc}")
        time.sleep(0.2)
    if not bodies:
        return problems
    body = bodies[0]
    for key, want in spec["expected"].items():
        got = body.get(key, "<missing>")
        if got != want:
            note = spec.get("deliberately_different", {}).get(key) or spec.get("known_gaps", {}).get(key)
            problems.append(f"{name}: {key} = {got!r}, expected {want!r}" + (f"  ({note})" if note else ""))
    if not body.get("commit_sha"):
        problems.append(f"{name}: commit_sha missing (deploy didn't pass --build-arg COMMIT_SHA?)")
    pids = sorted({(b.get("machine_id"), b.get("worker_pid")) for b in bodies})
    shas = sorted({b.get("commit_sha") for b in bodies}, key=str)
    print(f"{name}: commit {shas}, {len(pids)} distinct (machine, worker) over {len(bodies)} requests: {pids}")
    if len(shas) > 1:
        problems.append(f"{name}: machines report different commits {shas} (partial deploy?)")
    return problems


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", default=DEFAULT_EXPECTED)
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()
    with open(args.expected, encoding="utf-8") as fh:
        config = json.load(fh)
    problems = []
    for name, spec in config["environments"].items():
        problems.extend(check(name, spec, args.samples))
    if problems:
        print("\nDrift:\n" + "\n".join(f"  - {p}" for p in problems))
        return 1
    print("\nNo drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
