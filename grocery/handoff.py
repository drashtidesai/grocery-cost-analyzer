"""User-controlled verification in the worker's own visible guest browser."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable
from uuid import uuid4

from grocery.browser import StoreUnavailable, check_access


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class HumanHandoff:
    def __init__(self, control: Path, progress: Callable[[dict], None], area: str,
                 timeout: float = 900, clock: Callable[[], float] = time.monotonic):
        self.control = control
        self.progress = progress
        self.area = area
        self.clock = clock
        self.deadline = clock() + timeout

    def checkpoint(self) -> None:
        if read_json(self.control).get("action") == "cancel":
            raise StoreUnavailable("cancelled", "Noon browser session was cancelled. Amazon results remain available.")
        if self.clock() >= self.deadline:
            raise StoreUnavailable("handoff_timeout", "The Noon session expired after 15 minutes. Open a new verification session to retry.")

    def wait(self, page, store: str, phase: str) -> str | None:
        self.checkpoint()
        page.bring_to_front()
        wait_id = uuid4().hex
        message = ("Complete Cloudflare's human/robot check in the opened Noon browser, then click Continue Noon search."
                   if phase == "verification_required" else
                   f"Set the delivery area to {self.area} in the opened Noon browser, then click Continue Noon search.")
        event = {"phase": phase, "wait_id": wait_id, "store": store, "message": message, "done": 0, "total": 1}
        self.progress(event)
        handled = None
        while True:
            self.checkpoint()
            if page.is_closed():
                raise StoreUnavailable("cancelled", "The Noon browser was closed. Open a new verification session to retry.")
            command = read_json(self.control)
            if (command.get("action") == "continue" and command.get("wait_id") == wait_id
                    and command.get("id") and command["id"] != handled):
                handled = command["id"]
                try:
                    check_access(page)
                    if phase == "location_required":
                        from grocery.browser import confirmed_noon_area
                        if command.get("confirmed_area") == self.area:
                            return self.area
                        if not confirmed_noon_area(page, self.area):
                            self.progress({**event, "message": f"The delivery area is not confirmed as {self.area}. Select it in Noon, then continue again."})
                            continue
                    return
                except StoreUnavailable as exc:
                    if exc.code not in ("verification_required", "blocked"):
                        raise
                    self.progress({**event, "message": "Noon still requires verification. Finish the check in the same opened browser, then continue again."})
            # Pump browser events so the user's clicks/navigation are handled while the app stays responsive.
            page.wait_for_timeout(400)


def merge_noon_result(previous: dict | None, update: dict) -> dict:
    """Replace Noon data only; preserve Amazon prices, timestamps and selected product IDs."""
    merged = dict(previous or {})
    merged.pop("error", None)
    merged["stores"] = dict(merged.get("stores", {}))
    merged["products"] = {key: value for key, value in merged.get("products", {}).items() if not key.endswith("|Noon Minutes")}
    if update.get("error"):
        merged["stores"]["Noon Minutes"] = {"status": "error", "message": update["error"]}
    else:
        merged["stores"].update(update.get("stores", {}))
        merged["products"].update(update.get("products", {}))
        for key in ("area", "items"):
            if key in update:
                merged[key] = update[key]
        merged.setdefault("retrieved_at", update.get("retrieved_at", ""))
    return merged
