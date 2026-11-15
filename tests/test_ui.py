"""The counter app in a real browser (Playwright), against the fake Tally. Saves screenshots to tests/shots/."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHOTS = os.path.join(HERE, "shots")
os.makedirs(SHOTS, exist_ok=True)
TP, DP = 19133, 19134
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
    call("/api/settings", {"shop": {"name": "Shree Ganesh Kirana", "address": "Main Bazaar, Ajeetgarh, Sikar", "gstin": "08ABCPN1234F1Z2",
                                    "phone": "98765 43210", "fssai": "12226999000123"},
                           "upi": {"vpa": "ganeshkirana@okhdfcbank", "name": "Ganesh Kirana"}, "print": {"width": "80", "footer": "Thank you! Visit again."},
                           "public_url": "https://dukaan.example.in"}, tok)
    call("/api/cashier", {"name": "Ravi", "pin": "4321", "active": True, "can_discount": True, "can_return": True}, tok)
    call("/api/cashier", {"name": "Sunita", "pin": "1111", "active": True}, tok)
    for n in ("Tata Salt 1kg", "Parle-G 250g", "Surf Excel 1kg", "Amul Ghee 1L"):
        it = next(i for i in call("/api/items", tok=tok)["items"] if i["name"] == n)
        call("/api/item", {"name": n, "fav": True, "codes": it["codes"]}, tok)
    it = next(i for i in call("/api/items", tok=tok)["items"] if i["name"] == "Basmati Rice Loose")
    call("/api/item", {"name": "Basmati Rice Loose", "loose": True, "fav": True, "codes": []}, tok)
    call("/api/item", {"name": "Moong Dal Loose", "loose": True, "codes": []}, tok)
    call("/api/tally/setup", {"confirmed": True}, tok)

    with sync_playwright() as p:
        br = p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1366, "height": 820})
        pg = ctx.new_page()
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        pg.goto(BASE)
        pg.wait_for_selector("text=Ravi")
        pg.screenshot(path=f"{SHOTS}/01-login.png")
        pg.click("text=Ravi")
        for k in "4321":
            pg.click(f".keypad button[data-k='{k}']")
        pg.click(".keypad button[data-k='OK']")
        pg.wait_for_selector("text=Which counter is this?")
        pg.click(".sheet .it")
        pg.wait_for_selector("#q")
        # barcode typed into the search box (what a USB scanner does)
        pg.fill("#q", "8904043901015")
        pg.keyboard.press("Enter")
        pg.fill("#q", "8904043901015")
        pg.keyboard.press("Enter")
        pg.fill("#q", "surf")
        pg.wait_for_selector(".res")
        pg.screenshot(path=f"{SHOTS}/02-search.png")
        pg.keyboard.press("Enter")
        pg.click(".fav:has-text('Basmati')")
        pg.wait_for_selector("#qq")
        pg.click(".quick button[data-q='1']")
        pg.click("#qa")
        pg.keyboard.type("45")
        assert pg.input_value("#qa") == "45", "typing in the amount box"
        pg.screenshot(path=f"{SHOTS}/03-loose.png")
        pg.click("#qOk")
        pg.click(".fav:has-text('Ghee')")
        assert pg.locator(".ln").count() == 4, pg.locator(".ln").count()
        # quantity of salt is 2
        assert pg.locator(".ln").first.locator("input").input_value() == "2"
        pg.screenshot(path=f"{SHOTS}/04-cart.png")
        total = pg.locator(".tot .grand span").nth(1).inner_text()
        print("cart total", total)
        pg.click("#payBtn")
        pg.wait_for_selector(".paytot")
        pg.click("#tend")
        pg.keyboard.type("900")
        assert pg.input_value("#tend") == "900", "typing cash received"
        pg.screenshot(path=f"{SHOTS}/05-pay-cash.png")
        pg.click(".modes button[data-m='upi']")
        pg.wait_for_selector(".qr svg")
        pg.screenshot(path=f"{SHOTS}/06-pay-upi.png")
        pg.click(".modes button[data-m='cash']")
        pg.click(".quick button >> nth=1")
        pg.click("#pOk")
        pg.wait_for_selector("text=Bill saved")
        pg.screenshot(path=f"{SHOTS}/07-receipt.png")
        pg.click("#rNew")
        # sent and into Tally
        for _ in range(60):
            st = call("/api/tally", tok=tok)
            if st["counts"].get("posted"):
                break
            time.sleep(.25)
        assert st["counts"].get("posted") == 1, st["counts"]

        # offline: bill anyway, then it goes when back online
        ctx.set_offline(True)
        pg.fill("#q", "parle")
        pg.keyboard.press("Enter")
        pg.click("#payBtn")
        pg.click(".modes button[data-m='split']")
        pg.fill("[data-sp='cash']", "10")
        pg.screenshot(path=f"{SHOTS}/08-pay-split.png")
        pg.click("#pOk")
        pg.wait_for_selector("text=Bill saved")
        pg.click("#rNew")
        time.sleep(1)
        assert "to send" in pg.inner_text("#syncTxt"), pg.inner_text("#syncTxt")
        pg.screenshot(path=f"{SHOTS}/09-offline.png")
        ctx.set_offline(False)
        pg.evaluate("sync()")
        for _ in range(60):
            st = call("/api/tally", tok=tok)
            if st["counts"].get("posted", 0) >= 2:
                break
            time.sleep(.25)
        assert st["counts"].get("posted") == 2, st
        assert pg.inner_text("#syncTxt") == "Online"

        # customer with a GSTIN, then the bills list and a return
        pg.fill("#q", "ghee")
        pg.keyboard.press("Enter")
        pg.click("#custBtn")
        pg.fill("#cPh", "9829012345")
        pg.fill("#cNm", "Hotel Shyam")
        pg.fill("#cG", "08AAACR5055K1Z7")
        pg.screenshot(path=f"{SHOTS}/10-customer.png")
        pg.click("#cOk")
        pg.click("#payBtn")
        pg.click(".modes button[data-m='card']")
        pg.click("#pOk")
        pg.wait_for_selector("text=Bill saved")
        pg.click("#rNew")
        pg.goto(BASE + "/#bills")
        pg.wait_for_selector("#bList .it")
        time.sleep(1.5)
        pg.reload()
        pg.wait_for_selector("#bList .it")
        pg.screenshot(path=f"{SHOTS}/11-bills.png")
        pg.locator("#bList .it").last.click()
        pg.wait_for_selector("#dRet")
        pg.screenshot(path=f"{SHOTS}/12-bill-detail.png")
        pg.click("#dRet")
        pg.locator("[data-i='0']").fill("1")
        pg.click("#rCalc")
        pg.wait_for_selector("#rPrev .note")
        pg.click("#rGo")
        pg.wait_for_selector("text=Credit note")
        pg.screenshot(path=f"{SHOTS}/13-credit-note.png")

        # the owner's pages
        ctx2 = br.new_context(viewport={"width": 1366, "height": 900})
        o = ctx2.new_page()
        o.on("pageerror", lambda e: errors.append(str(e)))
        o.goto(BASE)
        o.click("#login .seg button[data-k='owner']")
        o.click("#lgPw")
        o.keyboard.type("owner123")
        assert o.input_value("#lgPw") == "owner123", "typing the owner password"
        o.click("#lgGo")
        o.wait_for_selector("text=Which counter is this?")
        o.click(".sheet .it[data-id='0']")
        o.wait_for_selector("#nav a")
        o.goto(BASE + "/#reports")
        o.wait_for_selector(".kpi")
        time.sleep(.5)
        o.screenshot(path=f"{SHOTS}/14-reports.png", full_page=True)
        o.goto(BASE + "/#tally")
        o.wait_for_selector("text=Where bills go in Tally")
        o.screenshot(path=f"{SHOTS}/15-tally.png", full_page=True)
        o.goto(BASE + "/#items")
        o.wait_for_selector("#iList .it")
        o.click("#iF button[data-f='bad']")
        o.screenshot(path=f"{SHOTS}/16-items.png")
        o.click("#iF button[data-f='all']")
        o.locator("#iList .it").first.click()
        o.wait_for_selector("#ip")
        o.screenshot(path=f"{SHOTS}/17-item-edit.png")
        o.goto(BASE + "/#settings")
        o.wait_for_selector("#sName")
        o.screenshot(path=f"{SHOTS}/18-settings.png", full_page=True)

        # labels: an item with an EAN barcode, and one that gets a shop barcode
        o.goto(BASE + "/#labels")
        o.wait_for_selector("#lQ")
        o.click("#lQ"); o.keyboard.type("basmati")
        o.click("#lRes .res")
        assert o.input_value("#lCode") == ""
        o.click("#lNew")
        o.wait_for_function("document.querySelector('#lCode').value.startsWith('21')")
        o.click("#lNet"); o.keyboard.type("1 kg")
        o.fill("#lName", "Basmati Rice Premium")
        o.click("#lPrice"); o.keyboard.press("Control+A"); o.keyboard.type("85")
        o.fill("#lMrp", "99")
        o.click("#lQuick button[data-m='6']")
        o.fill("#lCopies", "3")
        o.click("#lAdd")
        o.click("#lQ"); o.keyboard.type("tata salt")
        o.click("#lRes .res")
        assert o.input_value("#lCode") == "8904043901015"
        o.click("#lQuick button[data-m='12']")
        o.click("#lAdd")
        o.check("#oPack"); o.check("#oFssai")
        o.screenshot(path=f"{SHOTS}/23-labels.png", full_page=True)
        assert "Print 4 labels" in o.inner_text("#lPrint"), o.inner_text("#lPrint")
        o.evaluate("window.print = () => {}")
        o.click("#lPrint")
        time.sleep(.3)
        assert o.locator("#printarea .lbl").count() == 4
        o.emulate_media(media="print")
        o.screenshot(path=f"{SHOTS}/24-labels-print.png")
        o.emulate_media(media="screen")
        o.select_option("#lSize", "a4-65")
        o.fill("#lSkip", "7"); o.dispatch_event("#lSkip", "change")
        o.click("#lPrint")
        time.sleep(.3)
        o.emulate_media(media="print")
        o.screenshot(path=f"{SHOTS}/25-labels-a4.png")
        o.emulate_media(media="screen")
        bills_now = call("/api/items", tok=tok)["items"]
        assert any(c.startswith("21") for i in bills_now if i["name"] == "Basmati Rice Loose" for c in i["codes"])

        # cancel a bill that is already in Tally
        o.goto(BASE + "/#bills")
        o.wait_for_selector("#bList .it[data-uid]")
        o.locator("#bList .it[data-uid]", has_text="A/2627/2").first.click()
        o.wait_for_selector("#dCancel")
        o.click("#dCancel")
        o.wait_for_selector("#cWhy")
        o.fill("#cWhy", "billed twice")
        o.screenshot(path=f"{SHOTS}/26-cancel.png")
        o.click("#cYes")
        for _ in range(60):
            vs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{TP}/").read())["vouchers"]
            if any(v.get("cancelled") for v in vs):
                break
            time.sleep(.25)
        assert any(v.get("cancelled") and v["number"] == "A/2627/2" for v in vs), [(v["number"], v.get("cancelled")) for v in vs]

        # phone size
        m = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        mp = m.new_page()
        mp.on("pageerror", lambda e: errors.append(str(e)))
        mp.on("dialog", lambda d: d.accept())
        mp.goto(BASE)
        mp.click("text=Sunita")
        for k in "1111":
            mp.click(f".keypad button[data-k='{k}']")
        mp.click(".keypad button[data-k='OK']")
        mp.wait_for_selector("text=Which counter is this?")
        mp.screenshot(path=f"{SHOTS}/19-phone-counter.png")
        mp.click(".sheet .it")
        mp.wait_for_selector("#q")
        mp.click(".fav:has-text('Tata Salt')")
        mp.click(".fav:has-text('Parle')")
        mp.click(".fav:has-text('Surf')")
        mp.screenshot(path=f"{SHOTS}/20-phone-bill.png")
        mp.click("#pbPay")
        mp.wait_for_selector(".paytot")
        mp.screenshot(path=f"{SHOTS}/21-phone-pay.png")
        mp.click("#pOk")
        mp.wait_for_selector("text=Bill saved")
        mp.screenshot(path=f"{SHOTS}/22-phone-receipt.png")
        # print layout
        mp.click("#rNew")
        br.close()
    vs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{TP}/").read())["vouchers"]
    print("vouchers in fake Tally:", [(v["vtype"], v["number"]) for v in vs])
    print("browser errors:", errors)
    errors = [e for e in errors if "ERR_INTERNET_DISCONNECTED" not in e]
    assert not errors, errors
    print("UI TEST PASSED")
finally:
    srv.terminate()
    ft.terminate()
    shutil.rmtree(data, ignore_errors=True)
