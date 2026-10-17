#!/usr/bin/env python3
"""One Piece Vinted deal alerts — personal/non-commercial hobby prototype.

Listing discovery: authorised Soldgraph API (finite trial allowance).
Card baseline: TickerMint (USD/TCGplayer data, not UK sold prices).
Price confirmation: Soldgraph eBay UK sold listings, exact matching only.
No direct Vinted scraping or automatic purchasing.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import re
import smtplib
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "data" / "state.json"
CONFIG_FILE = ROOT / "config.json"
SOLDGRAPH_BASE = "https://api.soldgraph.com"
TICKERMINT_BASE = "https://api.tickermint.cards"
LOG = logging.getLogger("one-piece-monitor")
CODE_RE = re.compile(r"\b((?:OP|EB|ST|PRB)\s*\d{2}|P)\s*[- ]\s*(\d{3})\b", re.I)
BUNDLE_RE = re.compile(r"\b(bundle|collection|lot|bulk|booster|sealed|pack|box|proxy|replica|custom)\b", re.I)
BAD_LANG_RE = re.compile(r"\b(japanese|japan|jpn|jp\s*(?:version|card)|chinese|korean)\b", re.I)
GRADED_RE = re.compile(r"\b(?:psa|bgs|cgc)\s*\d{1,2}\b|\b(?:graded|slab)\b", re.I)


def api_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 35) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Personal-OnePiece-DealMonitor/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(3_000_000).decode("utf-8")


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 35) -> Any:
    return json.loads(api_text(url, headers=headers, timeout=timeout))


def save_json_atomic(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"next_search_index": 0, "seen": {}, "credits_used": 0, "total_alerts": 0}


def money(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("amount")
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
        return out if 0 <= out < 1_000_000 else None
    except (TypeError, ValueError):
        return None


def code_of(text: str) -> str | None:
    match = CODE_RE.search(text or "")
    if not match:
        return None
    return f"{match.group(1).replace(' ', '').upper()}-{match.group(2)}"


def variant_of(text: str) -> str | None:
    s = f" {re.sub(r'[_-]', ' ', str(text or '').lower())} "
    if any(w in s for w in ("manga", "super alternate art", "super alt art")):
        return "manga"
    if re.search(r"\b(sp|special rare|special card|special art)\b", s):
        return "sp"
    if any(w in s for w in ("alternate art", "alt art", "parallel", "full art")) or re.search(r"\b(aa|alt)\b", s):
        return "alt"
    if re.search(r"\b(standard|normal|regular|base art|non[- ]?alt)\b", s):
        return "regular"
    return None


def listing_identity(item: dict[str, Any], config: dict[str, Any]) -> tuple[str, str] | None:
    title = item.get("title") or ""
    if BUNDLE_RE.search(title) or BAD_LANG_RE.search(title) or GRADED_RE.search(title):
        return None
    if config.get("require_english_label") and not re.search(r"\benglish\b", title, re.I):
        return None
    code, variant = code_of(title), variant_of(title)
    return (code, variant) if code and variant else None


class CreditBudgetExhausted(Exception):
    pass


class SoldgraphClient:
    def __init__(self, token: str, state: dict[str, Any], cap: int):
        self.token = token
        self.state = state
        self.cap = cap

    def search(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if int(self.state.get("credits_used", 0)) >= self.cap:
            raise CreditBudgetExhausted(f"Demo/trial budget reached {self.cap} credits; scanner stopped safely")
        headers = {"Authorization": f"Bearer {self.token}", "Idempotency-Key": str(uuid.uuid4())}
        url = SOLDGRAPH_BASE + "/v1/" + endpoint.lstrip("/") + "?" + urllib.parse.urlencode(params)
        try:
            response = get_json(url, headers=headers)
            deadline = time.monotonic() + 120
            while response.get("status") == "pending":
                if time.monotonic() > deadline:
                    raise RuntimeError("Soldgraph job timed out (try the next scheduled run)")
                poll = response.get("poll_url", "")
                if not poll.startswith("/v1/jobs/"):
                    raise RuntimeError("Unexpected Soldgraph poll_url")
                response = get_json(SOLDGRAPH_BASE + poll + "?wait=20", headers={"Authorization": f"Bearer {self.token}"}, timeout=35)
            if response.get("status") != "complete":
                raise RuntimeError("Soldgraph: " + str(response.get("error", response.get("status"))))
            self.state["credits_used"] = int(self.state.get("credits_used", 0)) + int(response.get("credits", 1))
            return response.get("result", {})
        except urllib.error.HTTPError as e:
            explanation = e.read(1000).decode("utf-8", errors="replace")
            if e.code in (401, 403):
                raise RuntimeError("Soldgraph API key invalid or lacks access") from e
            if e.code == 429:
                raise RuntimeError("Soldgraph rate limit or trial credit quota reached: " + explanation[:200]) from e
            raise RuntimeError(f"Soldgraph HTTP {e.code}: {explanation[:200]}") from e


def extract_search_results(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if not isinstance(raw, dict):
        return []
    for key in ("results", "products", "cards", "data", "items", "hits"):
        value = raw.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            return extract_search_results(value)
    return []


def ticker_price(product: dict[str, Any]) -> float | None:
    for key in ("market_price_usd", "market_price", "latest_market", "latest_market_price", "market"):
        price = money(product.get(key))
        if price is not None:
            return price
    latest = product.get("latest") or product.get("price")
    if isinstance(latest, dict):
        return ticker_price(latest)
    return None


def ticker_market(code: str, variant: str) -> tuple[float, str, str] | None:
    import requests  # TickerMint blocks Python urllib; requests is required for these calls.
    url = TICKERMINT_BASE + "/products/search"
    response = requests.get(url, params={"q": code, "game": "one-piece"}, timeout=20)
    response.raise_for_status()
    products = extract_search_results(response.json())
    matches = []
    for product in products:
        title = " ".join(str(product.get(k) or "") for k in ("name", "title", "number", "card_number", "variant", "rarity"))
        if code_of(title) != code or variant_of(title) != variant:
            continue
        product_id = product.get("product_id") or product.get("id")
        if not product_id:
            continue
        price = ticker_price(product)
        if price is None:
            try:
                csv_url = TICKERMINT_BASE + "/products/" + urllib.parse.quote(str(product_id)) + ".csv"
                csv_response = requests.get(csv_url, timeout=20)
                csv_response.raise_for_status()
                rows = list(csv.DictReader(io.StringIO(csv_response.text)))
                price = money(rows[0].get("market_price_usd")) if rows else None
            except Exception as exc:
                LOG.warning("TickerMint product price unavailable for %s: %s", product_id, exc)
        if price and price > 0:
            page = product.get("tickermint_url") or f"https://tickermint.cards/card/{product_id}"
            matches.append((price, str(page), title))
    if len(matches) != 1:
        # Avoid a higher priced variant being mistaken for a lower priced printing.
        LOG.info("Ambiguous/missing TickerMint match for %s %s: %s candidates", code, variant, len(matches))
        return None
    return matches[0]


def current_usd_gbp() -> float:
    result = get_json("https://api.frankfurter.dev/v1/latest?base=USD&symbols=GBP", timeout=15)
    value = float(result["rates"]["GBP"])
    if not 0.4 < value < 1.2:
        raise ValueError("Unreasonable GBP exchange rate")
    return value


def uk_sold_prices(rows: list[dict[str, Any]], code: str, variant: str, require_english: bool) -> list[float]:
    prices: list[float] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=150)
    for row in rows:
        title = str(row.get("title") or "")
        if code_of(title) != code or variant_of(title) != variant:
            continue
        if BUNDLE_RE.search(title) or BAD_LANG_RE.search(title) or GRADED_RE.search(title):
            continue
        if require_english and not re.search(r"\benglish\b", title, re.I):
            continue
        if row.get("best_offer_accepted") or row.get("displayed_price_range"):
            continue
        date = row.get("sold_date")
        if not date:
            continue
        try:
            sold_date = datetime.fromisoformat(str(date).replace("Z", "+00:00"))
            if sold_date.tzinfo is None:
                sold_date = sold_date.replace(tzinfo=timezone.utc)
            if sold_date < cutoff:
                continue
        except ValueError:
            continue
        data = row.get("displayed_price") or {}
        if not isinstance(data, dict) or data.get("currency") != "GBP":
            continue
        amount = money(data)
        if amount is not None and amount > 0:
            prices.append(amount)
    return prices


def evaluate(item: dict[str, Any], sold_prices: list[float], benchmark_usd: float,
             exchange: float, config: dict[str, Any]) -> dict[str, Any] | None:
    identity = listing_identity(item, config)
    if not identity:
        return None
    item_price = item.get("displayed_price") or {}
    if not isinstance(item_price, dict) or item_price.get("currency") != "GBP":
        return None
    ask = money(item_price)
    if ask is None or ask > config["max_listing_price_gbp"]:
        return None
    total = item.get("total_price")
    if isinstance(total, dict) and total.get("currency") == "GBP":
        buyer_before_shipping = money(total)
    else:
        # If actual buyer protection fee missing, decline instead of guessing it.
        buyer_before_shipping = None
    if buyer_before_shipping is None or buyer_before_shipping < ask:
        return None
    verified = len(sold_prices) >= int(config["min_uk_sold_comps"])
    if config.get("require_uk_sold_confirmation", True) and not verified:
        return None
    resale = statistics.median(sold_prices) if verified else benchmark_usd * exchange
    resale = round(resale * (1 - float(config["resale_haircut_percent"]) / 100), 2)
    buy_total = round(buyer_before_shipping + float(config["shipping_in_gbp"]), 2)
    sale_cost = round(resale * float(config["selling_fee_percent"]) / 100 + float(config["shipping_out_gbp"]) + float(config["packaging_gbp"]), 2)
    profit = round(resale - buy_total - sale_cost, 2)
    roi = round(profit / buy_total * 100, 1) if buy_total else 0
    if profit < float(config["min_profit_gbp"]) or roi < float(config["min_roi_percent"]):
        return None
    # A very low ask can signal a counterfeit or wrong version: don't alert on it as a definite bargain.
    if verified and ask < statistics.median(sold_prices) * 0.10:
        return None
    discount = (resale - ask) / resale if resale else 0
    score = min(100, round(34 + min(36, roi * 0.32) + min(15, discount * 20) + (10 if verified and len(sold_prices) >= 5 else 6)))
    if not re.search(r"\benglish\b", item.get("title", ""), re.I):
        score = max(0, score - 9)
    if item.get("condition") not in ("very_good", "new_with_tags", "new_without_tags"):
        score = max(0, score - 7)
    if not verified:
        score = min(score, 70)
    if score < int(config["min_score"]):
        return None
    return {
        "id": str(item.get("id")), "title": item.get("title", ""), "url": item.get("link", ""),
        "image": item.get("image"), "code": identity[0], "variant": identity[1],
        "ask": ask, "buy_total": buy_total, "expected_resale": resale,
        "net_profit": profit, "roi": roi, "score": score, "sold_count": len(sold_prices),
        "benchmark_usd": round(benchmark_usd, 2), "confidence": "UK SOLD COMPARABLES" if verified else "US BENCHMARK ONLY",
        "note": "Card language/variant/condition/authenticity MUST be verified from photos before buying."
    }


def format_alert(deal: dict[str, Any], price_url: str) -> str:
    return (
        f"🔥 ONE PIECE VINTED DEAL — {deal['score']}/100\n"
        f"{deal['title']}\n"
        f"Card: {deal['code']} ({deal['variant']})\n"
        f"Price: £{deal['ask']:.2f} | Total buy est: £{deal['buy_total']:.2f}\n"
        f"UK resale (conservative): £{deal['expected_resale']:.2f}\n"
        f"Estimated net profit: £{deal['net_profit']:.2f} | ROI: {deal['roi']:.0f}%\n"
        f"UK sold comps: {deal['sold_count']} | {deal['confidence']}\n"
        f"{deal['note']}\n"
        f"Listing: {deal['url']}\n"
        f"TickerMint benchmark source: {price_url}"
    )


def send_telegram(message: str) -> bool:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    payload = urllib.parse.urlencode({"chat_id": chat, "text": message, "disable_web_page_preview": "false"}).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read())
    if not result.get("ok"):
        raise RuntimeError("Telegram delivery failed")
    return True


def send_email(message: str) -> bool:
    host, user, password = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    to = os.getenv("ALERT_EMAIL_TO")
    if not all((host, user, password, to)):
        return False
    outgoing = EmailMessage()
    outgoing["Subject"] = "🔥 One Piece Vinted card deal"
    outgoing["From"] = os.getenv("ALERT_EMAIL_FROM") or user
    outgoing["To"] = to
    outgoing.set_content(message)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT") or "587"), timeout=20) as client:
        client.starttls()
        client.login(user, password)
        client.send_message(outgoing)
    return True


def scan(state: dict[str, Any], config: dict[str, Any]) -> None:
    token = os.getenv("SOLDGRAPH_KEY")
    if not token:
        raise RuntimeError("Missing SOLDGRAPH_KEY; no live feed configured. See README.md")
    terms = config.get("search_terms") or []
    if not terms:
        raise RuntimeError("config.json must specify search_terms")
    idx = int(state.get("next_search_index", 0)) % len(terms)
    term = terms[idx]
    client = SoldgraphClient(token, state, int(config["max_total_api_credits"]))
    LOG.info("Checking Vinted UK newest results: %s (credits spent: %d)", term, state.get("credits_used", 0))
    result = client.search("vinted/listings", {"q": term, "country": "uk", "sort": "newest", "page": 1,
                                               "max_price": int(config["max_listing_price_gbp"])})
    state["next_search_index"] = (idx + 1) % len(terms)
    state["last_query"] = term
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    rows = (result.get("data") or [])[:int(config["max_listing_rows_per_run"])]
    seen = state.setdefault("seen", {})
    state["total_listings_checked"] = int(state.get("total_listings_checked", 0)) + len(rows)
    if not rows:
        LOG.info("No results for %s", term)
        return
    exchange = None
    sold_checks = 0
    for item in rows:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        ask = money(item.get("displayed_price"))
        if ask is None:
            continue
        prev = seen.get(item_id)
        if prev and (not config.get("enable_price_drop_realert") or ask > prev.get("last_ask", ask) - float(config["min_price_drop_gbp"])):
            continue
        identity = listing_identity(item, config)
        if not identity:
            seen[item_id] = {"last_ask": ask, "status": "unconfirmed_identity"}
            continue
        code, variant = identity
        try:
            if exchange is None:
                exchange = current_usd_gbp()
            benchmark = ticker_market(code, variant)
        except Exception as e:
            LOG.warning("Could not benchmark %s: %s", code, e)
            continue  # retry transient upstream failure in later scans
        if not benchmark:
            seen[item_id] = {"last_ask": ask, "status": "variant_not_matched"}
            continue
        benchmark_usd, price_url, _ = benchmark
        total_price = money(item.get("total_price"))
        if total_price is None:
            LOG.info("Skipping %s because buyer fee not known", item_id)
            continue
        gross_buy = total_price + float(config["shipping_in_gbp"])
        conservative_us_estimate = benchmark_usd * exchange * (1 - float(config["resale_haircut_percent"]) / 100)
        approx_net = conservative_us_estimate * (1 - float(config["selling_fee_percent"]) / 100) - float(config["shipping_out_gbp"]) - float(config["packaging_gbp"]) - gross_buy
        if approx_net < float(config["min_profit_gbp"]) or approx_net / max(gross_buy, 0.01) * 100 < float(config["min_roi_percent"]):
            seen[item_id] = {"last_ask": ask, "status": "low_benchmark_margin"}
            continue
        if sold_checks >= int(config["max_sold_checks_per_run"]):
            LOG.info("Sold comp budget for this run exhausted; will retry %s next time", item_id)
            continue
        query = f"{code} {('manga' if variant == 'manga' else 'alternate art' if variant == 'alt' else 'SP' if variant == 'sp' else 'regular')} one piece"
        try:
            sold_result = client.search("ebay/sold", {"q": query, "country": "uk", "count": 40, "page": 1})
            sold_checks += 1
        except CreditBudgetExhausted:
            raise
        except Exception as e:
            LOG.warning("eBay sold check unavailable for %s: %s", item_id, e)
            continue
        sold = uk_sold_prices(sold_result.get("data") or [], code, variant, bool(re.search(r"\benglish\b", item.get("title", ""), re.I)))
        deal = evaluate(item, sold, benchmark_usd, exchange, config)
        if not deal:
            seen[item_id] = {"last_ask": ask, "status": "not_qualified", "uk_comps": len(sold)}
            continue
        message = format_alert(deal, price_url)
        delivered = False
        for channel in (send_telegram, send_email):
            try:
                delivered = channel(message) or delivered
            except Exception as e:
                LOG.error("Notification delivery failure (%s): %s", channel.__name__, e)
        if delivered:
            seen[item_id] = {"last_ask": ask, "status": "alerted", "score": deal["score"], "at": datetime.now(timezone.utc).isoformat()}
            state["total_alerts"] = int(state.get("total_alerts", 0)) + 1
            LOG.info("SENT: %s; profit £%.2f; ROI %.0f%%", code, deal["net_profit"], deal["roi"])
        else:
            LOG.warning("QUALIFIED but no working Telegram/email channel configured: %s", message)
    # Bound Git history growth: only retain most recently observed IDs.
    max_seen = int(config.get("retain_seen_items", 2500))
    if len(seen) > max_seen:
        for key in list(seen)[: len(seen) - max_seen]:
            seen.pop(key, None)


def demo() -> None:
    cfg = json.loads(CONFIG_FILE.read_text())
    item = json.loads((ROOT / "demo" / "listings.json").read_text())["result"]["data"][0]
    mock = json.loads((ROOT / "demo" / "valuation.json").read_text())
    cfg["min_score"] = 0
    deal = evaluate(item, mock["uk_sold_gbp"], mock["benchmark_usd"], mock["usd_gbp_rate"], cfg)
    print("DEMO / MADE-UP PRICES — DOES NOT CONTACT VINTED, EBAY OR TELEGRAM")
    print("DO NOT TREAT THESE NUMBERS AS REAL SELLING PRICES\n")
    print(format_alert(deal, "https://tickermint.cards/")) if deal else print("Demo sample did not pass the selected deal thresholds")


def main() -> int:
    parser = argparse.ArgumentParser(description="One Piece Vinted deals")
    parser.add_argument("--demo", action="store_true", help="Offline demo with invented prices and fake URLs")
    parser.add_argument("--test-alert", action="store_true", help="Send clearly-labelled notification without listing API credits")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.demo:
        demo()
        return 0
    if args.test_alert:
        message = "✅ TEST ONLY: One Piece Vinted Monitor notifications are connected. No real deal was found."
        sent = False
        for channel in (send_telegram, send_email):
            try:
                sent = channel(message) or sent
            except Exception as exc:
                LOG.error("Test notification channel %s failed: %s", channel.__name__, exc)
        if not sent:
            LOG.error("No alert delivered. Add Telegram or SMTP secrets (see README).")
            return 1
        print("Test alert delivered to at least one channel")
        return 0
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    state = load_state()
    try:
        scan(state, config)
        state["last_error"] = None
        return_code = 0
    except (CreditBudgetExhausted, RuntimeError) as exc:
        LOG.error("Scanner stopped: %s", exc)
        state["last_error"] = str(exc)
        return_code = 1
    except Exception as exc:
        LOG.exception("Unexpected scanner failure: %s", exc)
        state["last_error"] = str(exc)
        return_code = 1
    finally:
        save_json_atomic(STATE_FILE, state)
    return return_code


if __name__ == "__main__":
    sys.exit(main())
