import json
from pathlib import Path
import re

html = Path(".runtime/noon-layout/page.html").read_text(encoding="utf-8")
for label, pattern in [("card", r'<a[^>]*data-testid="catalog-product-box-[^>]+>.*?</a>'),
                       ("search", r'<form[^>]*data-testid="search-input-form".*?</form>'),
                       ("address", r'<button[^>]*class="[^"]*addressButton[^>]*>.*?</button>')]:
    match = re.search(pattern, html, re.S)
    print(json.dumps({label: match.group(0)[:7000] if match else None}))
