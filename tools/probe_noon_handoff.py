"""Verify the visible Noon worker reaches a human handoff; never complete the check."""
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from browser_worker import atomic_json
from grocery.handoff import read_json


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    directory = root / ".runtime" / ("handoff-probe-" + uuid4().hex)
    directory.mkdir(parents=True)
    request, output, control = directory / "request.json", directory / "result.json", directory / "control.json"
    atomic_json(request, {"area": "Dubai Marina", "stores": ["Noon Minutes"], "interactive": True,
                          "items": [{"id": "0", "name": "Potato", "weight": "1kg"}]})
    process = subprocess.Popen([sys.executable, str(root / "browser_worker.py"), "--request", str(request), "--output", str(output), "--control", str(control)], cwd=root)
    deadline = time.monotonic() + 80
    try:
        while process.poll() is None and time.monotonic() < deadline:
            event = read_json(output.with_suffix(".progress.json"))
            if event.get("phase") in ("verification_required", "location_required"):
                print(json.dumps({"observed_handoff": event["phase"], "message": event["message"], "check_completed": False}), flush=True)
                break
            time.sleep(0.5)
        else:
            print(json.dumps({"result": read_json(output), "progress": read_json(output.with_suffix(".progress.json"))}), flush=True)
    finally:
        atomic_json(control, {"action": "cancel", "id": uuid4().hex})
        process.wait(timeout=45)
        print(json.dumps({"exit_code": process.returncode, "result": read_json(output)}), flush=True)
