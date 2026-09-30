"""`curie-infusion evaluate BUNDLE --as-of ISO` -> JSON safety state on stdout."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .fhir import BundleIndex, parse_time
from .ledger import compute_ledger, reconcile
from .rules import evaluate_flags, load_rules


def evaluate(bundle: dict, rules: dict, as_of: datetime, window_start: datetime | None = None) -> dict:
    idx = BundleIndex(bundle)
    return {
        "as_of": as_of.isoformat(),
        "rules_version": rules["schema_version"],
        "flags": [asdict(f) for f in evaluate_flags(idx, rules, as_of)],
        "ledger": [asdict(o) for o in compute_ledger(idx, as_of, window_start=window_start)],
        "reconciliation": reconcile(idx),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curie-infusion")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate", help="evaluate one patient Bundle")
    ev.add_argument("bundle", type=Path)
    ev.add_argument("--as-of", required=True, help="ISO-8601 timestamp with timezone")
    ev.add_argument("--window-start", help="ISO-8601 start of the ledger window")
    ev.add_argument("--rules", type=Path, help="rules JSON (default: packaged v0.1)")
    args = parser.parse_args(argv)

    state = evaluate(
        json.loads(args.bundle.read_text()),
        load_rules(args.rules),
        as_of=parse_time(args.as_of),
        window_start=parse_time(args.window_start),
    )
    json.dump(state, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
