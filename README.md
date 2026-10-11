# Grocery Cost Analyzer

A four-tab Streamlit app managed with uv, comparing whole-pack grocery costs from Amazon Now and Noon Minutes in AED.

## Run locally

### Download from GitHub

On the repository page, click **Code → Download ZIP** and extract the ZIP. Open a terminal in the extracted project folder (the folder containing `app.py` and `pyproject.toml`). You can also clone the repository using Git if you prefer.

### Install and start

You need an internet connection for installation and live store searches, and a desktop computer for the visible Noon verification browser. The project uses Python 3.12; uv can install the required Python version for you.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from this folder:

```powershell
uv sync
uv run playwright install chromium
uv run streamlit run app.py --server.address 127.0.0.1 --server.port 8502
```

Open http://localhost:8502. Python 3.12 is selected by `.python-version`; dependencies are recorded in `uv.lock`.

On Linux, if Chromium reports missing system libraries, run `uv run playwright install --with-deps chromium` and follow the system permission prompts. Stop the app with **Ctrl+C** in the terminal. To start it again later, run the last Streamlit command above.

If port 8502 is already in use, choose another port such as `--server.port 8503` and open the matching localhost URL. If `uv` is not recognized after installation, close and reopen your terminal.

## Use the app

1. In **Grocery Input**, enter your Dubai delivery area. The provided `Grocery List.csv` loads initially. Upload a replacement CSV, edit the table, or add items manually.
2. Click **Search both stores**. In **Store Prices**, review store access status and select a product for each item at each store. If Noon requires a human check, click **Open Noon verification session**, complete Cloudflare's check in the browser window opened by the app, then click **Continue Noon search** in Streamlit. Keep that browser window open; the app continues in that exact guest session. Different brands and attributes are explicitly offered as alternatives requiring selection.
3. In **Item Comparison**, green means a cheaper whole-pack item total and red means a costlier total. Ties and missing prices are neutral. Review pack count, purchased quantity, excess, unit price and product links.
4. In **Basket Totals**, an overall winner appears only when both lists have all items selected. Incomplete totals are labelled subtotals, with a separate subtotal for items selected at both stores.

CSV format (UTF-8, optional BOM):

```csv
Item name,Weight
Banana Rasakadali,200-250g
Potato,1kg
Full Cream Milk Al Rawabi,2 Litres
```

Supported quantities include grams, kilograms, millilitres, litres, pieces and multipacks such as `6 x 200ml`. Requested ranges use their upper bound. Variable-weight product ranges use their lower bound so enough quantity is purchased. For example, a 750g request and a 500g pack uses two packs and shows 250g excess. For a 1kg request and a 900–1000g product, two packs are required to guarantee the requested weight.

## Guest access and live-data limits

- No app account or retailer login is needed or attempted. Store links point to the supplied public storefronts, but the app cannot prevent a retailer from showing its own sign-in or verification screen.
- Every search uses fresh, isolated guest browser contexts. No existing browser account, saved address, or authentication cookies are read. Noon verification and the subsequent search share one visible guest context; verification is not transferred from an unrelated browser tab.
- An entered delivery area must be confirmed through the retailer's guest location control before its prices are used. A default area or truncated location label is insufficient.
- Amazon Now search stays within the Now search index and seller. Ordinary Amazon marketplace prices are excluded. Displayed guest pack prices are used, not crossed-out prices or unit-price figures.
- **Live checks during development:** Amazon Now confirmed the saved guest area and returned prices for the supplied list, including the requested Rasakadali bananas, Al Rawabi milk and 160g Nada yoghurt. No suitable fresh-cherry candidate was found in the final targeted check. Noon Minutes returned HTTP 403 with Cloudflare security verification. The app now provides a human handoff for this check. Post-verification Noon location/search parsing still needs verification against a user-cleared session. The app does not solve or bypass Cloudflare checks, fabricate prices, or declare a winner from missing results.
- **Noon human handoff:** The visible browser opens on the computer running Streamlit, so this flow is intended for the local desktop app. The rest of the app stays responsive and Amazon results are preserved. If Noon needs a delivery-area selection, select the requested area in that same browser and continue again. If its address label doesn't expose the full area, tick the confirmation that you selected a delivery location in the requested area; results label this as **Delivery area confirmed by you**. Additional security checks during search pause for another handoff. Continuing early leaves the session open and prompts you to finish. Closing the browser, cancelling, changing the grocery list/area, or the 15-minute session expiry ends the handoff; you can open a new one. The app does not log in.
- If access is blocked, use **Add a manually checked guest price** in Store Prices. Check the product for your delivery area, enter its pack price and quantity, and confirm availability. Such entries remain labelled **Manual guest price** in results and exports. This fallback does not count as automated retrieval.
- Unknown or incompatible pack sizes cannot contribute to totals. Product brand, variety, fat level, preparation and other attributes require user review.
- Totals include products only. Delivery/service fees, checkout adjustments, coupons, membership discounts and conditional promotions are excluded. Prices may change before checkout. List/area changes invalidate earlier search results and manual entries for that comparison.

Browser collection runs in a separate Python process to avoid Windows/Streamlit asyncio conflicts. Local request/result/progress files are kept under the git-ignored `.runtime/` folder for diagnostics. They contain the entered area and grocery list; do not share that folder unintentionally.

## Verify

```powershell
uv run pytest -q
```

Tests cover the provided CSV, quantity conversion, multipacks, whole-pack rounding, excess, variable-weight produce, ties, incomplete baskets, conservative guest-price parsing, and Streamlit interactions including manual alternatives and area invalidation. Test prices are fixtures and never shown as live store data.

For an optional live check with the public sample area Al Barsha 1:

```powershell
uv run python tools/smoke_live.py
```

The scripts in `tools/` are development diagnostics. Store markup can change and may require adapter updates. Automated access must be available from the machine running Streamlit; this app does not rely on a proxy or authentication service.
