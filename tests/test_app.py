import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from streamlit.testing.v1 import AppTest


def by_label(elements, label):
    return next(element for element in elements if element.label == label)


def test_app_load_and_add_item():
    app = AppTest.from_file(str(Path("app.py").resolve())).run(timeout=30)
    assert not app.exception
    assert len(app.tabs) == 4
    assert app.session_state["rows"][0]["Item name"] == "Banana Rasakadali"
    assert by_label(app.button, "Search both stores").disabled
    by_label(app.text_input, "Dubai delivery area").set_value("Dubai Marina").run()
    assert not app.exception
    assert not by_label(app.button, "Search both stores").disabled
    by_label(app.text_input, "Item name").set_value("Rice")
    by_label(app.text_input, "Weight / volume / count").set_value("1kg")
    by_label(app.button, "Add to grocery list").click().run()
    assert not app.exception
    assert len(app.session_state["rows"]) == 11


def test_manual_price_comparison_and_area_invalidation():
    app = AppTest.from_file(str(Path("app.py").resolve())).run(timeout=30)
    app.session_state["rows"] = [{"Item name": "Potato", "Weight": "750g"}]
    app.session_state["editor_version"] = 1
    by_label(app.text_input, "Dubai delivery area").set_value("Dubai Marina").run()
    for store, price in [("Amazon Now", 5.5), ("Noon Minutes", 4.0)]:
        by_label(app.selectbox, "Store").select(store)
        by_label(app.text_input, "Actual product name and brand").set_value("Fresh Potato")
        by_label(app.text_input, "Pack size").set_value("500g")
        by_label(app.number_input, "Displayed pack price (AED)").set_value(price)
        app.checkbox[0].check()
        by_label(app.button, "Add manual alternative").click().run()
        assert not app.exception
    for store in ("Amazon Now", "Noon Minutes"):
        chooser = by_label(app.selectbox, f"Selected product · {store}")
        chooser.select_index(1).run()
        assert not app.exception
    assert any("Noon Minutes is cheaper overall" in entry.value for entry in app.success)
    assert any("Save AED 3.00" in entry.value for entry in app.success)
    by_label(app.text_input, "Dubai delivery area").set_value("Al Barsha 1").run()
    assert not app.exception
    assert not any("cheaper overall" in entry.value for entry in app.success)
    assert all(metric.value == "—" for metric in app.metric if "subtotal" in metric.label)


def test_noon_handoff_buttons_and_completion_preserve_amazon_selection(monkeypatch):
    from grocery.models import Product
    app = AppTest.from_file(str(Path("app.py").resolve())).run(timeout=30)
    app.session_state["rows"] = [{"Item name": "Potato", "Weight": "1kg"}]
    app.session_state["editor_version"] = 1
    by_label(app.text_input, "Dubai delivery area").set_value("Dubai Marina").run()
    token = hashlib.sha256(json.dumps({"area": "dubai marina", "items": [{"id": "0", "name": "Potato", "weight": "1kg"}]}, sort_keys=True).encode()).hexdigest()
    amazon = Product("amazon-choice", "Amazon Now", "Fresh Potato 500g", "500g", "5.50", "https://www.amazon.ae/dp/EXAMPLE123", True, "2026-10-11T00:00:00+00:00", "Dubai Marina").to_dict()
    app.session_state["result_token"] = token
    app.session_state["result"] = {"area": "Dubai Marina", "retrieved_at": "2026-10-11T00:00:00+00:00",
                                   "stores": {"Amazon Now": {"status": "ready"}, "Noon Minutes": {"status": "verification_required"}},
                                   "products": {"0|Amazon Now": {"candidates": [amazon]}}}
    app.run()
    by_label(app.selectbox, "Selected product · Amazon Now").select_index(1).run()
    assert any(m.value == "AED 11.00" for m in app.metric)
    process = Mock()
    process.poll.return_value = None

    def launch(command, **kwargs):
        request = Path(command[command.index("--request") + 1])
        payload = json.loads(request.read_text(encoding="utf-8"))
        assert payload["stores"] == ["Noon Minutes"] and payload["interactive"] is True
        output = Path(command[command.index("--output") + 1])
        output.with_suffix(".progress.json").write_text(json.dumps({"phase": "verification_required", "wait_id": "current-check", "message": "Complete the human check."}), encoding="utf-8")
        return process

    monkeypatch.setattr("subprocess.Popen", launch)
    by_label(app.button, "Open Noon verification session").click().run()
    assert not app.exception
    assert any(m.value == "AED 11.00" for m in app.metric)
    job = app.session_state["noon_job"]
    by_label(app.button, "Continue Noon search").click().run()
    command = json.loads(Path(job["control"]).read_text(encoding="utf-8"))
    assert command["action"] == "continue" and command["wait_id"] == "current-check"
    Path(job["output"]).with_suffix(".progress.json").write_text(json.dumps({"phase": "location_required", "wait_id": "area-check", "message": "Select Dubai Marina in Noon."}), encoding="utf-8")
    app.run()
    by_label(app.checkbox, "I selected a delivery location in Dubai Marina in this Noon browser.").check()
    by_label(app.button, "Continue Noon search").click().run()
    command = json.loads(Path(job["control"]).read_text(encoding="utf-8"))
    assert command["confirmed_area"] == "Dubai Marina" and command["wait_id"] == "area-check"
    noon = {**amazon, "id": "noon-choice", "store": "Noon Minutes", "price": "4.00", "url": "https://minutes.noon.com/uae-en/potato/p/123"}
    Path(job["output"]).write_text(json.dumps({"stores": {"Noon Minutes": {"status": "ready", "confirmed_area": "Dubai Marina"}},
                                            "products": {"0|Noon Minutes": {"candidates": [noon]}}}), encoding="utf-8")
    app.run()
    assert not app.exception
    assert by_label(app.selectbox, "Selected product · Amazon Now").index == 1
    assert "noon_job" not in app.session_state
    by_label(app.selectbox, "Selected product · Noon Minutes").select_index(1).run()
    assert any("Noon Minutes is cheaper overall" in entry.value for entry in app.success)
    app.session_state["result"]["stores"]["Noon Minutes"]["status"] = "verification_required"
    app.run()
    by_label(app.button, "Open Noon verification session").click().run()
    second_job = app.session_state["noon_job"]
    by_label(app.text_input, "Dubai delivery area").set_value("Al Barsha 1").run()
    assert json.loads(Path(second_job["control"]).read_text(encoding="utf-8"))["action"] == "cancel"
    assert "noon_job" not in app.session_state
    assert not any("cheaper overall" in entry.value for entry in app.success)
