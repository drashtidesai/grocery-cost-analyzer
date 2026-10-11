"""Standalone browser worker, avoiding Windows asyncio conflicts in Streamlit.

Usage: python browser_worker.py --request PATH --output PATH
"""
import argparse
import json
import os
from pathlib import Path

from grocery.browser import collect_prices
from grocery.handoff import HumanHandoff


def atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--control", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        report = lambda event: atomic_json(args.output.with_suffix(".progress.json"), event)
        handoff = None
        if request.get("interactive") is True:
            if args.control is None:
                raise ValueError("An interactive Noon session needs a control file.")
            handoff = HumanHandoff(args.control, report, request.get("area", ""))

        def progress(event):
            if handoff:
                handoff.checkpoint()
            report(event)

        result = collect_prices(request, progress, handoff.wait if handoff else None)
        atomic_json(args.output, result)
    except Exception as exc:
        atomic_json(args.output, {"error": str(exc)[:500]})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
