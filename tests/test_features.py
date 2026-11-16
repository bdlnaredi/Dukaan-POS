"""Live stock, batches and expiry, offers, loyalty points and day-end cash, against the fake Tally.
Run: python3 tests/test_features.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))
import gst as G  # noqa: E402

TP, DP = 19143, 19144
data = tempfile.mkdtemp()
env = dict(os.environ, APP_PASSWORD="owner123", DATA_DIR=data, PORT=str(DP))
ft = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_tally.py"), str(TP)])
srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "main.py")], env=env)
TODAY = date.today().isoformat()


def call(path, body=None, tok=None, expect=200):
    req = urllib.request.Request(f"http://127.0.0.1:{DP}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **({"X-Dukaan-Token": tok} if tok else {})})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    assert code == expect, (path, code, text)
    return json.loads(text) if text.startswith(("{", "[")) else text


def wait(cond, secs=30):
    for _ in range(secs * 4):
        v = cond()
        if v:
            return v
        time.sleep(.25)
    raise AssertionError("timed out")


try:
    for _ in range(40):
        try:
            call("/api/hello")
            break
        except Exception:
            time.sleep(.25)
    tok = call("/api/login", {"kind": "owner", "password": "owner123", "device": "dev-1"})["token"]
    call("/api/tally/connect", {"url": f"127.0.0.1:{TP}"}, tok)
    call("/api/tally/refresh", {}, tok)
    call("/api/tally/setup", {"confirmed": True}, tok)
    call("/api/settings", {"loyalty": {"enabled": True, "per100": 1, "value": 100, "min": 10, "max_pct": 50}}, tok)
    fy = list(call("/api/counter/claim", {"counter_id": 1}, tok)["numbers"])[0]
    items = {i["name"]: i for i in call("/api/items", tok=tok)["items"]}
    assert items["Parle-G 250g"]["stock"] == 40

    n = [0]

    def bill(lines, pays=None, buyer=None, **kw):
        n[0] += 1
        b = {"uid": f"f{n[0]:04d}-{os.urandom(4).hex()}", "counter_id": 1, "number": f"A/{fy}/{n[0]}", "date": TODAY,
             "time": "10:00:00", "created": time.time(), "lines": lines, "buyer": buyer or {}, "round": True, **kw}
        c = G.compute(b)
        b["amount"] = c["amount"]
        b["payments"] = pays if pays is not None else [{"mode": "cash", "amount": c["amount"]}]
        r = call("/api/bills", {"bills": [b]}, tok)["results"][0]
        assert r["status"] == "ok", r
        return b, c, r

    def L(name, qty, disc=0):
        i = items[name]
        return {"item": name, "qty": qty, "price": i["price"], "mrp": i["mrp"], "gst": i["gst"], "hsn": i["hsn"], "disc": disc}

    # ---- live stock: drops at once, and isn't taken off twice once Tally has the bill and is copied again
    call("/api/item", {"name": "Parle-G 250g", "min_stock": 37, "codes": items["Parle-G 250g"]["codes"]}, tok)
    b1, c1, _ = bill([L("Parle-G 250g", "3")])
    st = call("/api/stock", tok=tok)["stock"]
    assert st["Parle-G 250g"] == 37, st["Parle-G 250g"]
    it = next(i for i in call("/api/items", tok=tok)["items"] if i["name"] == "Parle-G 250g")
    assert it["low"], it
    wait(lambda: call(f"/api/bill/{b1['uid']}", tok=tok)["tally"] == "posted")
    # the fake Tally doesn't reduce its own stock, so after a fresh copy Tally "still" says 40: that's what Tally says
    call("/api/tally/refresh", {}, tok)
    assert call("/api/stock", tok=tok)["stock"]["Parle-G 250g"] == 40
    print("live stock ok")

    # ---- batches: first expiry first, expired skipped, returns go back, cancel releases
    soon = (date.today() + timedelta(days=10)).isoformat()
    later = (date.today() + timedelta(days=200)).isoformat()
    past = (date.today() - timedelta(days=2)).isoformat()
    call("/api/batch", {"item": "Amul Ghee 1L", "no": "OLD", "qty": "5", "mfd": "2026-01-01", "exp": past}, tok)
    call("/api/batch", {"item": "Amul Ghee 1L", "no": "B2", "qty": "2", "mfd": "2026-06-01", "exp": later}, tok)
    call("/api/batch", {"item": "Amul Ghee 1L", "no": "B1", "qty": "3", "mfd": "2026-05-01", "exp": soon}, tok)
    call("/api/batch", {"item": "Amul Ghee 1L", "no": "X", "qty": "1", "mfd": later, "exp": soon}, tok, expect=400)
    b2, c2, _ = bill([L("Amul Ghee 1L", "4")])
    bs = {b["no"]: b for b in call("/api/batches?filter=all&item=Amul%20Ghee%201L", tok=tok)["batches"]}
    assert (bs["OLD"]["left"], bs["B1"]["left"], bs["B2"]["left"]) == ("5", "0", "1"), bs
    exp = call("/api/batches?filter=expiring", tok=tok)["batches"]
    assert [b["no"] for b in exp] == ["OLD"], exp          # B1 is sold out; OLD is expired
    r = call("/api/return", {"uid": "ret-f001-abcd", "orig_uid": b2["uid"], "counter_id": 1, "lines": [{"index": 0, "qty": "1"}],
                             "refund": "cash"}, tok)
    bs = {b["no"]: b for b in call("/api/batches?filter=all&item=Amul%20Ghee%201L", tok=tok)["batches"]}
    assert bs["B2"]["left"] == "2", bs                      # came back to the batch it was taken from last
    print("batches ok")

    # ---- batch names to Tally when asked
    call("/api/tally/setup", {"confirmed": True, "send_batches": True}, tok)
    b3, c3, _ = bill([L("Amul Ghee 1L", "1")])
    wait(lambda: call(f"/api/bill/{b3['uid']}", tok=tok)["tally"] == "posted")
    v = [x for x in json.loads(urllib.request.urlopen(f"http://127.0.0.1:{TP}/").read())["vouchers"] if x["number"] == b3["number"]][0]
    assert "<BATCHNAME>B2</BATCHNAME>" in v["xml"], v["xml"][:800]
    call("/api/tally/setup", {"confirmed": True, "send_batches": False}, tok)
    print("batch names to Tally ok")

    # ---- loyalty: earn, use, take back on return, undo on cancel
    ph = "9829012345"
    b4, c4, _ = bill([L("Aashirvaad Atta 5kg", "4")], buyer={"phone": ph, "name": "Meena"})     # 4 x 265 = 1060 -> 10 points
    cust = call("/api/loyalty?phone=" + ph, tok=tok)["customer"]
    assert cust["points"] == c4["amount"] // 10000, cust
    pts = cust["points"]
    b5, c5, r5 = bill([L("Surf Excel 1kg", "1")], buyer={"phone": ph}, bill_disc=pts * 100, loyalty={"points": pts, "value": pts * 100})
    assert not r5["flag"], r5
    cust = call("/api/loyalty?phone=" + ph, tok=tok)["customer"]
    assert cust["points"] == c5["amount"] // 10000, cust          # all used, then earned on the rest
    # using more than they have: kept, marked for the owner
    _, _, r6 = bill([L("Surf Excel 1kg", "1")], buyer={"phone": ph}, bill_disc=5000, loyalty={"points": 50, "value": 5000})
    assert "had only" in r6["flag"], r6
    before = call("/api/loyalty?phone=" + ph, tok=tok)["customer"]["points"]
    call("/api/return", {"uid": "ret-f002-abcd", "orig_uid": b4["uid"], "counter_id": 1, "lines": [{"index": 0, "qty": "2"}], "refund": "cash"}, tok)
    after = call("/api/loyalty?phone=" + ph, tok=tok)["customer"]["points"]
    assert after == max(0, before - 5), (before, after)
    b7, c7, _ = bill([L("Amul Ghee 1L", "2")], buyer={"phone": "9000000001"})
    assert call("/api/loyalty?phone=9000000001", tok=tok)["customer"]["points"] == c7["amount"] // 10000
    call(f"/api/bill/{b7['uid']}/cancel", {"reason": "test"}, tok)
    assert call("/api/loyalty?phone=9000000001", tok=tok)["customer"]["points"] == 0
    print("loyalty ok")

    # ---- offers are recorded and reported
    call("/api/offer", {"kind": "bxgy", "name": "Buy 2 get 1 Parle", "items": ["Parle-G 250g"], "buy": 2, "free": 1}, tok)
    call("/api/offer", {"kind": "bill", "name": "Rs 20 off 500", "min_bill": 50000, "amt": 2000}, tok)
    call("/api/offer", {"kind": "off", "name": "bad"}, tok, expect=400)
    assert len(call("/api/boot", tok=tok)["offers"]) == 2
    bill([dict(L("Parle-G 250g", "3"), disc=2500)], offers=[{"id": 1, "name": "Buy 2 get 1 Parle", "amount": 2500}])
    s = call(f"/api/reports/summary?from={TODAY}&to={TODAY}", tok=tok)
    assert s["offers"][0]["name"] == "Buy 2 get 1 Parle" and s["offers"][0]["amount"] == 2500, s["offers"]
    assert s["points"]["used"] >= pts
    print("offers ok")

    # ---- day end
    sh = call("/api/shift?counter_id=1", tok=tok)
    assert sh["shift"] is None
    t0 = time.time()
    call("/api/shift/open", {"counter_id": 1, "opening": 200000}, tok)
    call("/api/shift/open", {"counter_id": 1, "opening": 0}, tok, expect=400)
    time.sleep(.05)
    b8, c8, _ = bill([L("Tata Salt 1kg", "10")], created=time.time())                               # cash 280
    b9, c9, _ = bill([L("Tata Salt 1kg", "5")], pays=[{"mode": "upi", "amount": 14000}], created=time.time())
    call("/api/shift/move", {"counter_id": 1, "kind": "out", "amount": 5000, "reason": "milk"}, tok)
    call("/api/return", {"uid": "ret-f003-abcd", "orig_uid": b8["uid"], "counter_id": 1, "lines": [{"index": 0, "qty": "1"}], "refund": "cash"}, tok)
    sh = call("/api/shift?counter_id=1", tok=tok)
    m = sh["summary"]
    assert (m["cash"], m["upi"], m["refund_cash"], m["cash_out"]) == (28000, 14000, 2800, 5000), m
    assert m["expected"] == 200000 + 28000 - 2800 - 5000, m
    closed = call("/api/shift/close", {"counter_id": 1, "denoms": {"50000": 4, "10000": 2, "200": 1}, "kept": 100000, "note": "ok"}, tok)["closed"]
    assert closed["counted"] == 220200 and closed["diff"] == 220200 - m["expected"], closed
    assert call("/api/shift?counter_id=1", tok=tok)["shift"] is None
    assert call("/api/shift?counter_id=1", tok=tok)["last_kept"] == 100000
    hist = call(f"/api/shifts?from={TODAY}&to={TODAY}", tok=tok)["shifts"]
    assert len(hist) == 1 and hist[0]["diff"] == closed["diff"], hist
    print("day end ok")
    print("ALL FEATURE TESTS PASSED")
finally:
    srv.terminate()
    ft.terminate()
    shutil.rmtree(data, ignore_errors=True)
