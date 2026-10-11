"""Use a public sample area to verify the actual worker's guest search flow."""
import json
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grocery.browser import collect_prices
from grocery.models import read_csv, validate_rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Check banana, potato, cherries and yoghurt only")
    args = parser.parse_args()
    items, errors = validate_rows(read_csv(Path("Grocery List.csv").read_bytes()))
    assert not errors
    if args.quick:
        items = [i for i in items if i.id in {"0", "2", "4", "8"}]
    result = collect_prices({"area": "Al Barsha 1", "items": [i.to_dict() for i in items]},
                            lambda event: print(event["message"], flush=True))
    target = Path(".runtime/live-smoke.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stores": result["stores"], "searches": {k: {"status": v["status"], "message": v["message"], "count": len(v["candidates"]),
                                                              "first": [{"name": p["name"], "pack_size": p["pack_size"], "price": p["price"]} for p in v["candidates"][:1]]} for k, v in result["products"].items()}}, ensure_ascii=False), flush=True)
