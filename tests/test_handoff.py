import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from grocery.browser import StoreUnavailable, collect_prices
from grocery.handoff import HumanHandoff, merge_noon_result


def write_command(path, **command):
    path.write_text(json.dumps(command), encoding="utf-8")


def test_handoff_waits_for_user_and_does_not_accept_early_continue(tmp_path, monkeypatch):
    control = tmp_path / "control.json"
    page = Mock()
    page.is_closed.return_value = False
    events = []
    clock = [0]
    checks = [0]

    def check(same_page):
        assert same_page is page
        checks[0] += 1
        if checks[0] == 1:
            raise StoreUnavailable("verification_required", "Still waiting")

    monkeypatch.setattr("grocery.handoff.check_access", check)

    def progress(event):
        events.append(event)
        write_command(control, action="continue", id=str(len(events)), wait_id=event["wait_id"])

    def pump(_):
        clock[0] += 0.4

    page.wait_for_timeout.side_effect = pump
    handoff = HumanHandoff(control, progress, "Dubai Marina", clock=lambda: clock[0])
    handoff.wait(page, "Noon Minutes", "verification_required")
    assert checks[0] == 2
    assert "still requires verification" in events[-1]["message"]
    page.bring_to_front.assert_called_once()
    page.goto.assert_not_called()  # Retains the actual challenged page, without navigating away.
    page.close.assert_not_called()


def test_stale_continue_is_ignored_and_cancel_is_observed(tmp_path):
    control = tmp_path / "control.json"
    write_command(control, action="continue", wait_id="old-challenge", id="old")
    page = Mock()
    page.is_closed.return_value = False
    events = []
    page.wait_for_timeout.side_effect = lambda _: write_command(control, action="cancel")
    handoff = HumanHandoff(control, events.append, "Dubai Marina")
    with pytest.raises(StoreUnavailable, match="cancelled"):
        handoff.wait(page, "Noon Minutes", "verification_required")
    assert events[0]["wait_id"] != "old-challenge"


def test_closed_browser_and_session_timeout(tmp_path):
    page = Mock()
    page.is_closed.return_value = True
    handoff = HumanHandoff(tmp_path / "control.json", lambda _: None, "Dubai Marina")
    with pytest.raises(StoreUnavailable, match="browser was closed"):
        handoff.wait(page, "Noon Minutes", "verification_required")
    clock = [0]
    handoff = HumanHandoff(tmp_path / "control.json", lambda _: None, "Dubai Marina", timeout=1, clock=lambda: clock[0])
    clock[0] = 2
    with pytest.raises(StoreUnavailable) as exc:
        handoff.checkpoint()
    assert exc.value.code == "handoff_timeout"


def test_location_requires_store_label_or_explicit_user_confirmation(tmp_path, monkeypatch):
    control = tmp_path / "control.json"
    page = Mock()
    page.is_closed.return_value = False
    monkeypatch.setattr("grocery.handoff.check_access", lambda _: None)
    monkeypatch.setattr("grocery.browser.confirmed_noon_area", lambda p, a: "")
    events = []

    def progress(event):
        events.append(event)
        write_command(control, action="continue", id=str(len(events)), wait_id=event["wait_id"],
                      confirmed_area="Dubai Marina" if len(events) == 2 else None)

    handoff = HumanHandoff(control, progress, "Dubai Marina")
    assert handoff.wait(page, "Noon Minutes", "location_required") == "Dubai Marina"
    assert "area is not confirmed" in events[-1]["message"]


def test_verification_and_later_search_challenge_reuse_one_context(monkeypatch):
    page, context, browser = Mock(), Mock(), Mock()
    context.new_page.return_value = page
    browser.new_context.return_value = context
    chromium = Mock()
    chromium.launch.return_value = browser
    manager = Mock()
    manager.__enter__ = Mock(return_value=SimpleNamespace(chromium=chromium))
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("grocery.browser.sync_playwright", lambda: manager)
    page.goto.return_value = SimpleNamespace(status=403)
    page.url = "https://minutes.noon.com/uae-en/"
    challenged = [True]
    attempts = [0]

    def check(current, status=None):
        assert current is page
        if challenged[0]:
            raise StoreUnavailable("verification_required", "Human check")
        assert status is None  # HTTP 403 from before verification must not be reused.

    monkeypatch.setattr("grocery.browser.check_access", check)
    monkeypatch.setattr("grocery.browser.set_noon_area", lambda p, a: "Dubai Marina")

    def cards(current, store, query):
        assert current is page
        attempts[0] += 1
        if attempts[0] == 1:
            challenged[0] = True
            raise StoreUnavailable("verification_required", "Another human check")
        return [{"name": "Fresh Potato 500g", "price": "AED 4.00", "url": "/uae-en/product/potato/p/123", "text": "Available"}]

    monkeypatch.setattr("grocery.browser.search_cards", cards)
    handed_pages = []

    def user_handoff(current, store, phase):
        handed_pages.append(current)
        assert phase == "verification_required"
        challenged[0] = False  # Simulates a human-completed check; no CAPTCHA interaction occurs in tests.

    result = collect_prices({"area": "Dubai Marina", "stores": ["Noon Minutes"], "interactive": True,
                             "items": [{"id": "0", "name": "Potato", "weight": "1kg"}]}, human_control=user_handoff)
    assert handed_pages == [page, page]
    chromium.launch.assert_called_once_with(headless=False)
    browser.new_context.assert_called_once()
    assert result["stores"]["Noon Minutes"]["status"] == "ready"
    assert result["products"]["0|Noon Minutes"]["candidates"][0]["price"] == "4.00"
    context.close.assert_called_once()


def test_noon_merge_preserves_amazon_and_clears_old_noon():
    amazon = {"candidates": [{"id": "amazon-selected"}]}
    original = {"stores": {"Amazon Now": {"status": "ready"}, "Noon Minutes": {"status": "verification_required"}},
                "products": {"0|Amazon Now": amazon, "0|Noon Minutes": {"candidates": [{"id": "stale"}]}},
                "retrieved_at": "old-search-time"}
    result = merge_noon_result(original, {"stores": {"Noon Minutes": {"status": "ready"}},
                                          "products": {"0|Noon Minutes": {"candidates": [{"id": "new"}]}}})
    assert result["products"]["0|Amazon Now"] == amazon
    assert result["products"]["0|Noon Minutes"]["candidates"][0]["id"] == "new"
    assert original["products"]["0|Noon Minutes"]["candidates"][0]["id"] == "stale"
    assert result["retrieved_at"] == "old-search-time"
