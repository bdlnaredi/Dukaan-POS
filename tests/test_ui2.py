"""Offers, points, live stock, batches and day end in a real browser. Screenshots go to tests/shots/."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = os.path.join(HERE, "shots")
os.makedirs(SHOTS, exist_ok=True)
TP, DP = 19153, 19154
BASE = f"http://127.0.0.1:{DP}"
data = tempfile.mkdtemp()
env = dict(os.environ, APP_PASSWORD="owner123", DATA_DIR=data, PORT=str(DP))
ft = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_tally.py"), str(TP)])
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "main.py")], env=env)


def call(path, body=None, tok=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **({"X-Dukaan-Token": tok} if tok else {})})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


errors = []
try:
    for _ in range(40):
        try:
            call("/api/hello")
            break
        except Exception:
            time.sleep(.25)
    tok = call("/api/login", {"kind": "owner", "password": "owner123", "device": "setup"})["token"]
    call("/api/tally/connect", {"url": f"127.0.0.1:{TP}"}, tok)
    call("/api/tally/refresh", {}, tok)
    call("/api/tally/setup", {"confirmed": True}, tok)
    call("/api/settings", {"shop": {"name": "Shree Ganesh Kirana", "address": "Main Bazaar, Ajeetgarh", "gstin": "08ABCPN1234F1Z2", "phone": "9876543210"},
                           "loyalty": {"enabled": True, "per100": 1, "value": 100, "min": 5, "max_pct": 50}}, tok)
    call("/api/cashier", {"name": "Ravi", "pin": "4321", "active": True, "can_discount": True, "can_return": True}, tok)
    items = {i["name"]: i for i in call("/api/items", tok=tok)["items"]}
    for n in ("Parle-G 250g", "Surf Excel 1kg", "Amul Ghee 1L", "Aashirvaad Atta 5kg"):
        call("/api/item", {"name": n, "fav": True, "codes": items[n]["codes"], "min_stock": 38 if n == "Parle-G 250g" else None}, tok)
    call("/api/offer", {"kind": "bxgy", "name": "Buy 2 get 1 Parle-G", "items": ["Parle-G 250g"], "buy": 2, "free": 1}, tok)
    call("/api/offer", {"kind": "off", "name": "10% off detergent", "groups": ["Detergent"], "pct": 10}, tok)
    call("/api/offer", {"kind": "bill", "name": "Rs 25 off bills of Rs 1000", "min_bill": 100000, "amt": 2500}, tok)
    past = (date.today() - timedelta(days=3)).isoformat()
    soon = (date.today() + timedelta(days=12)).isoformat()
    call("/api/batch", {"item": "Amul Ghee 1L", "no": "G-0926", "qty": "2", "mfd": "2026-06-01", "exp": soon}, tok)
    call("/api/batch", {"item": "Surf Excel 1kg", "no": "S-OLD", "qty": "4", "mfd": "2025-01-01", "exp": past}, tok)
    # a customer who already has points
    call("/api/loyalty/adjust", {"phone": "9829012345", "points": 60, "note": "opening balance"}, tok)

    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1366, "height": 820})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE)
        pg.click("text=Ravi")
        for k in "4321":
            pg.click(f".keypad button[data-k='{k}']")
        pg.click(".keypad button[data-k='OK']")
        pg.wait_for_selector("text=Which counter is this?")
        pg.click(".sheet .it")
        pg.wait_for_selector("#dOpen")
        pg.screenshot(path=f"{SHOTS}/30-day-start.png")
        pg.fill("#dOpen", "2000")
        pg.click("#dGo")
        pg.wait_for_selector("#q")
        for _ in range(3):
            pg.click(".fav:has-text('Parle')")
        pg.click(".fav:has-text('Surf')")          # only an expired batch: warned
        pg.wait_for_selector("#toast:has-text('expiry')")
        pg.click(".fav:has-text('Atta')")
        pg.click(".fav:has-text('Atta')")
        pg.click(".fav:has-text('Atta')")
        pg.click(".fav:has-text('Ghee')")
        assert "Buy 2 get 1 Parle-G" in pg.inner_text("#lines")
        assert "10% off detergent" in pg.inner_text("#lines")
        assert "Rs 25 off" in pg.inner_text("#tot")
        pg.click("#custBtn")
        pg.fill("#cPh", "9829012345")
        pg.wait_for_selector("#cPts:has-text('60 points')")
        pg.screenshot(path=f"{SHOTS}/31-customer-points.png")
        pg.click("#cOk")
        pg.screenshot(path=f"{SHOTS}/32-cart-offers.png")
        pg.click("#payBtn")
        pg.wait_for_selector("#ptsUse")
        before = pg.inner_text(".paytot .a")
        pg.fill("#ptsN", "40")
        pg.click("#ptsUse")
        pg.wait_for_selector("#ptsX")
        after = pg.inner_text(".paytot .a")
        assert before != after, (before, after)
        pg.screenshot(path=f"{SHOTS}/33-pay-points.png")
        pg.click("#pOk")
        pg.wait_for_selector("text=Bill saved")
        txt = pg.inner_text(".preview")
        assert "Offer: Buy 2 get 1 Parle-G" in txt and "Points used: 40" in txt, txt
        pg.screenshot(path=f"{SHOTS}/34-receipt-offers.png")
        pg.click("#rNew")
        for _ in range(40):
            r = call("/api/loyalty?phone=9829012345", tok=tok)["customer"]
            if r["points"] != 60:
                break
            time.sleep(.25)
        print("points after:", r["points"])
        assert r["points"] > 20, r
        # live stock: Parle-G went 40 -> 37, under its reorder level
        st = call("/api/stock", tok=tok)["stock"]
        assert st["Parle-G 250g"] == 37, st["Parle-G 250g"]
        # day end
        pg.click("#menuBtn")
        pg.click("#mDay")
        pg.wait_for_selector("#dClose")
        pg.screenshot(path=f"{SHOTS}/35-day.png")
        pg.click("#dClose")
        pg.wait_for_selector("[data-d='50000']")
        pg.fill("[data-d='50000']", "4")
        pg.fill("[data-d='10000']", "3")
        pg.fill("#dKeep", "2000")
        pg.screenshot(path=f"{SHOTS}/36-day-count.png")
        pg.click("#dDone")
        pg.wait_for_selector("text=Day closed")
        pg.screenshot(path=f"{SHOTS}/37-day-closed.png")

        # owner pages
        o = br.new_context(viewport={"width": 1366, "height": 900}).new_page()
        o.on("pageerror", lambda e: errors.append(str(e)))
        o.goto(BASE)
        o.click("#login .seg button[data-k='owner']")
        o.click("#lgPw")
        o.keyboard.type("owner123")
        o.click("#lgGo")
        o.click(".sheet .it[data-id='0']")
        o.wait_for_selector("#nav a")
        o.goto(BASE + "/#offers")
        o.wait_for_selector("#oList .it")
        o.wait_for_selector("#lyList .it")
        o.screenshot(path=f"{SHOTS}/38-offers.png")
        o.click("#oNew")
        o.select_option("#ofK", "off")
        o.screenshot(path=f"{SHOTS}/39-offer-new.png")
        o.click(".sheet .x")
        o.goto(BASE + "/#items")
        o.wait_for_selector("#iList .it")
        o.click("#iF button[data-f='low']")
        assert "Parle-G" in o.inner_text("#iList")
        o.click("#iF button[data-f='exp']")
        o.wait_for_selector("#iList .it:has-text('Surf')")
        o.screenshot(path=f"{SHOTS}/40-expiring.png")
        o.click("#iF button[data-f='all']")
        o.locator("#iList .it", has_text="Amul Ghee").first.click()
        o.wait_for_selector("#ibList table")
        o.screenshot(path=f"{SHOTS}/41-item-batches.png")
        o.click("[data-lb]")
        o.wait_for_selector("#lName")
        assert o.input_value("#lName") == "Amul Ghee 1L" and o.input_value("#lExp") == soon
        o.goto(BASE + "/#reports")
        o.wait_for_selector("text=Day closings")
        time.sleep(.5)
        o.screenshot(path=f"{SHOTS}/42-reports.png", full_page=True)
        br.close()
    print("browser errors:", errors)
    assert not errors, errors
    print("UI2 TEST PASSED")
finally:
    srv.terminate()
    ft.terminate()
    shutil.rmtree(data, ignore_errors=True)
