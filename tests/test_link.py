"""Dukaan linked with StoreKart (the online store), end to end, against the fake Tally.

Needs a StoreKart checkout: STOREKART_DIR=/path/to/storekart python3 tests/test_link.py
(skipped when it isn't there).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SK = os.environ.get("STOREKART_DIR") or os.path.join(os.path.dirname(ROOT), "storekart")
if not os.path.exists(os.path.join(SK, "server.js")):
    print("StoreKart not found, skipping (set STOREKART_DIR)")
    sys.exit(0)

TP, DP, SP = 19131, 19132, 19133
d_data, s_data = tempfile.mkdtemp(), tempfile.mkdtemp()
procs = {}


def start_dukaan():
    procs["dukaan"] = subprocess.Popen([sys.executable, os.path.join(ROOT, "app", "main.py")],
                                       env=dict(os.environ, APP_PASSWORD="owner123", DATA_DIR=d_data, PORT=str(DP)))


procs["tally"] = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_tally.py"), str(TP)])
start_dukaan()
procs["storekart"] = subprocess.Popen(["node", os.path.join(SK, "server.js")], env=dict(os.environ, DATA_DIR=s_data, PORT=str(SP)),
                                      stdout=subprocess.DEVNULL)


def call(port, path, body=None, tok=None, expect=200, hdr=None):
    h = {"Content-Type": "application/json"}
    if tok and port == DP:
        h["X-Dukaan-Token"] = tok
    elif tok:
        h["Authorization"] = "Bearer " + tok
    h.update(hdr or {})
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET", headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        code, text = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode()
    if expect is not None:
        assert code == expect, (port, path, code, text)
    return json.loads(text) if text.startswith(("{", "[")) else text


def dk(path, body=None, tok=None, expect=200):
    return call(DP, path, body, tok, expect)


def sk(path, body=None, tok=None, expect=200, method=None):
    if method in ("PUT", "PATCH", "DELETE"):
        req = urllib.request.Request(f"http://127.0.0.1:{SP}{path}", data=json.dumps(body or {}).encode(), method=method,
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            r = urllib.request.urlopen(req, timeout=30)
            code, text = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            code, text = e.code, e.read().decode()
        assert expect is None or code == expect, (path, code, text)
        return json.loads(text)
    return call(SP, path, body, tok, expect)


def wait(fn, what, secs=30):
    for _ in range(secs * 4):
        v = fn()
        if v:
            return v
        time.sleep(.25)
    raise AssertionError("timed out waiting for " + what)


def ok(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    assert cond, msg


try:
    for _ in range(60):
        try:
            dk("/api/hello"); sk("/api/health"); break
        except Exception:
            time.sleep(.25)

    # ---------- Dukaan with Tally, a cashier
    otok = dk("/api/login", {"kind": "owner", "password": "owner123", "device": "dev-owner"})["token"]
    dk("/api/tally/connect", {"url": f"127.0.0.1:{TP}"}, otok)
    dk("/api/tally/refresh", {}, otok)
    dk("/api/settings", {"shop": {"name": "Shree Ganesh Kirana", "address": "Main Bazaar", "gstin": "08AAACR5055K1Z7", "phone": "9876543210"}}, otok)
    dk("/api/tally/setup", {"confirmed": True}, otok)
    dk("/api/cashier", {"name": "Ravi", "pin": "4321", "active": True, "can_discount": True}, otok)
    ok(dk("/api/hello")["storekart_login"] is False, "link off: Dukaan login screen has no online-store option")
    call(DP, "/api/link/stock", hdr={"X-Link-Key": "dk_wrong"}, expect=401)

    # ---------- StoreKart: a grocery store with staff
    sys.path.insert(0, HERE)
    sk("/api/setup", {"password": "secret123", "adminName": "Bharat", "username": "admin", "products": [],
                      "config": {"name": "Ganesh Online", "phone": "9876543210", "businessType": "grocery", "modes": ["delivery", "pickup"],
                                 "payments": ["cod", "upi"], "upiId": "ganesh@okhdfcbank", "categories": ["Grocery"],
                                 "delivery": {"fee": 25, "freeAbove": 499, "minOrder": 0}, "labels": {}, "features": {},
                                 "tax": {"enabled": True, "rate": 5, "inclusive": True, "gstin": "08AAACR5055K1Z7"}}})
    A = sk("/api/login", {"username": "admin", "password": "secret123"})["token"]
    for n, u, r in (("Rahul", "rahul", "manager"), ("Suresh", "suresh", "cashier"), ("Ramesh", "ramesh", "delivery")):
        sk("/api/users", {"name": n, "username": u, "role": r, "password": "1234"}, A, expect=201)
    T = {u: sk("/api/login", {"username": u, "password": "1234"})["token"] for u in ("rahul", "suresh", "ramesh")}
    sk("/api/login", {"username": "Ravi", "password": "4321"}, expect=401)
    ok(True, "link off: Dukaan's cashier can't sign in to StoreKart")

    # ---------- link them
    L = dk("/api/link/settings", {"enabled": True, "storekart_url": f"http://127.0.0.1:{SP}"}, otok)
    key = L["key"]
    ok(key.startswith("dk_") and L["online_counter"] == "Online store", "Dukaan makes a link key and an Online store counter")
    st = sk("/api/link/settings", {"enabled": True, "url": f"127.0.0.1:{DP}", "key": key, "staffLogin": True}, A)
    ok(st["enabled"] and st["shop"] == "Shree Ganesh Kirana", f"StoreKart connected to Dukaan ({st['shop']})")
    ok(dk("/api/link/test", {}, otok)["ok"], "Dukaan can reach StoreKart with the same key")
    sk("/api/link/settings", tok=T["rahul"], expect=403)
    ok(True, "only owner/admin can change the link")

    # ---------- items: import from Dukaan
    imp = sk("/api/link/import", {"groups": ["Grocery", "Dairy", "Detergent", "Biscuits"]}, A)
    prods = {p["name"]: p for p in sk("/api/products")}
    ok(imp["imported"] == 7 and "Mystery Item" not in prods, "7 sellable Dukaan items imported (not the one without GST)")
    salt, atta = prods["Tata Salt 1kg"], prods["Aashirvaad Atta 5kg"]
    ok(salt["price"] == 28 and salt["gstRate"] == 5 and salt["hsn"] == "25010020" and salt["stock"] == 40 and salt["link"]["item"] == "Tata Salt 1kg",
       "imported with Dukaan's price, GST, HSN and stock 40")

    # ---------- an online order puts stock aside in Dukaan
    o1 = sk("/api/orders", {"customer": {"name": "Priya", "phone": "9123456780", "address": "22 Civil Lines"}, "mode": "delivery", "payment": "cod",
                            "items": [{"productId": salt["id"], "qty": 3}, {"productId": atta["id"], "qty": 2}]}, expect=201)
    live = wait(lambda: (lambda s: s if s["Tata Salt 1kg"] == 37 else None)(call(DP, "/api/link/stock", hdr={"X-Link-Key": key})["stock"]), "reservation")
    ok(live["Tata Salt 1kg"] == 37 and live["Aashirvaad Atta 5kg"] == 38, "order placed → Dukaan live stock 40 → 37 salt, 38 atta (put aside)")
    boot = dk("/api/boot", tok=otok)
    ok({i["name"]: i["stock"] for i in boot["items"]}["Tata Salt 1kg"] == 37, "Dukaan counters see 37 salt too")
    sk("/api/link/sync", {}, A)
    ok({p["name"]: p for p in sk("/api/products")}["Tata Salt 1kg"]["stock"] == 37, "StoreKart shows Dukaan's live stock (37)")
    r = sk("/api/orders", {"customer": {"name": "X", "phone": "9123456780", "address": "Y"}, "mode": "delivery",
                           "items": [{"productId": salt["id"], "qty": 50}]}, expect=409)
    ok("Only 37" in r["error"], "can't order more than Dukaan has: " + r["error"])

    # counter sells 30 salt in Dukaan → online shows 7
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import gst as G
    ctok = dk("/api/login", {"kind": "cashier", "name": "Ravi", "pin": "4321", "device": "dev-1"})["token"]
    claim = dk("/api/counter/claim", {"counter_id": 1}, ctok)
    fy = list(claim["numbers"])[0]
    line = {"item": "Tata Salt 1kg", "qty": "30", "price": 2800, "mrp": 2800, "gst": 5, "hsn": "25010020", "disc": 0}
    b = {"uid": "cnt-0001-abcd", "counter_id": 1, "number": f"A/{fy}/1", "date": time.strftime("%Y-%m-%d"), "time": "10:00:00", "lines": [line], "round": True, "buyer": {}}
    amt = G.compute(b)["amount"]
    b.update(amount=amt, payments=[{"mode": "cash", "amount": amt}])
    dk("/api/bills", {"bills": [b]}, ctok)
    sk("/api/link/sync", {}, A)
    ok({p["name"]: p for p in sk("/api/products")}["Tata Salt 1kg"]["stock"] == 7, "counter sale in Dukaan → online store shows 7 left")

    # ---------- completed + paid → a Dukaan bill in series W, into Tally
    oid = o1["id"]
    rid = [u for u in sk("/api/users", tok=A) if u["username"] == "ramesh"][0]["id"]
    sk(f"/api/orders/{oid}", {"status": "accepted"}, T["rahul"], method="PATCH")
    sk(f"/api/orders/{oid}", {"assignTo": rid}, T["rahul"], method="PATCH")
    sk(f"/api/orders/{oid}", {"status": "ready"}, T["suresh"], method="PATCH")
    sk(f"/api/orders/{oid}", {"status": "out"}, T["ramesh"], method="PATCH")
    sk(f"/api/orders/{oid}", {"paid": True}, T["ramesh"], method="PATCH")
    sk(f"/api/orders/{oid}", {"status": "delivered"}, T["ramesh"], method="PATCH")
    bill = wait(lambda: next((x for x in dk("/api/bills", tok=otok)["bills"] if x["number"].startswith("W/")), None), "online bill")
    ok(bill["number"] == f"W/{fy}/1" and bill["amount"] == 3 * 2800 + 2 * 26500 and not bill["flag"],
       f"delivered + paid → Dukaan bill {bill['number']} for ₹{bill['amount'] / 100:.2f}, nothing flagged")
    full = dk(f"/api/bill/{bill['uid']}", tok=otok)
    ok(full["data"]["payments"][0]["mode"] == "cash" and full["data"]["buyer"]["phone"] == "9123456780", "paid in cash, customer's mobile on the bill (loyalty)")
    live = call(DP, "/api/link/stock", hdr={"X-Link-Key": key})["stock"]
    ok(live["Tata Salt 1kg"] == 7, "stock stays right: reservation became the bill (7 salt)")
    ok(dk("/api/link/settings", tok=otok)["reserved"] == [], "nothing left put aside")
    tally = wait(lambda: (lambda s: s if any(v.get("number") == f"W/{fy}/1" for v in s["vouchers"]) else None)(
        json.loads(urllib.request.urlopen(f"http://127.0.0.1:{TP}/", timeout=5).read())), "voucher in Tally")
    ok(True, f"bill W/{fy}/1 went into Tally as a sales voucher")
    o = sk(f"/api/orders/{oid}", tok=A)
    ok(o["dk"]["billed"] and any("Billed in Dukaan" in h["text"] for h in o["history"]), "staff see “Billed in Dukaan as W/…” on the order")
    pub = sk(f"/api/orders/{oid}")
    ok("dk" not in pub and not any("Dukaan" in h["text"] for h in pub["history"]), "customers don't see Dukaan notes")

    # ---------- cancelled order gives stock back
    o2 = sk("/api/orders", {"customer": {"name": "Amit", "phone": "9000000000"}, "mode": "pickup", "payment": "upi",
                            "items": [{"productId": salt["id"], "qty": 2}]}, expect=201)
    wait(lambda: call(DP, "/api/link/stock", hdr={"X-Link-Key": key})["stock"]["Tata Salt 1kg"] == 5, "reserve 2")
    sk(f"/api/orders/{o2['id']}", {"status": "cancelled"}, T["rahul"], method="PATCH")
    wait(lambda: call(DP, "/api/link/stock", hdr={"X-Link-Key": key})["stock"]["Tata Salt 1kg"] == 7, "release")
    ok(True, "cancelled order → its 2 salt go back (7)")

    # ---------- logins both ways
    hello = dk("/api/hello")
    ok(hello["storekart_login"], "Dukaan login screen offers the online store login")
    r = dk("/api/login", {"kind": "storekart", "username": "rahul", "password": "1234", "device": "dev-9"})
    ok(r["me"]["role"] == "cashier" and r["me"]["name"] == "Rahul", "StoreKart manager Rahul signs in to Dukaan as a cashier")
    r = dk("/api/login", {"kind": "storekart", "username": "admin", "password": "secret123", "device": "dev-9"})
    ok(r["me"]["role"] == "owner", "StoreKart admin signs in to Dukaan as the owner")
    r = dk("/api/login", {"kind": "storekart", "username": "ramesh", "password": "1234"}, expect=401)
    ok("Delivery" in r["error"], "delivery boy can't use the Dukaan counter")
    dk("/api/login", {"kind": "storekart", "username": "rahul", "password": "nope"}, expect=401)
    ok(True, "wrong StoreKart password refused by Dukaan")
    r = sk("/api/login", {"username": "Ravi", "password": "4321"})
    ok(r["user"]["role"] == "cashier" and r["user"]["fromDukaan"], f"Dukaan cashier Ravi signs in to StoreKart (as {r['user']['username']})")
    r2 = sk("/api/login", {"username": "Ravi", "password": "4321"})
    ok(r2["user"]["id"] == r["user"]["id"], "…and gets the same StoreKart account next time")
    r = sk("/api/login", {"username": "owner", "password": "owner123"})
    ok(r["user"]["role"] == "owner", "Dukaan owner signs in to StoreKart as owner")
    sk("/api/login", {"username": "Ravi", "password": "0000"}, expect=401)
    ok(True, "wrong Dukaan PIN refused by StoreKart")

    # ---------- Dukaan down: StoreKart carries on and catches up
    procs["dukaan"].terminate(); procs["dukaan"].wait()
    o3 = sk("/api/orders", {"customer": {"name": "Neha", "phone": "9111111111", "address": "Z"}, "mode": "delivery", "payment": "cod",
                            "items": [{"productId": salt["id"], "qty": 1}, {"productId": atta["id"], "qty": 2}]}, expect=201)
    ok(o3["number"] > 0, "Dukaan down: online order still placed")
    q = wait(lambda: (lambda s: s if s["queue"] and s["queue"][0]["error"] else None)(sk("/api/link/settings", tok=A)), "queued")
    ok(q["queue"][0]["kind"] == "reserve" and q["queue"][0]["error"], "…its stock message waits in the queue: " + q["queue"][0]["error"])
    r = sk("/api/login", {"username": "suresh", "password": "1234"})
    ok(r["user"]["username"] == "suresh", "StoreKart logins still work while Dukaan is down")
    start_dukaan()
    for _ in range(60):
        try:
            dk("/api/hello"); break
        except Exception:
            time.sleep(.25)
    sk("/api/link/retry", {}, A)
    wait(lambda: not sk("/api/link/settings", tok=A)["queue"], "queue drained", 40)
    # (the fake Tally always says 40 in stock; real Tally takes the bills off, so only the reservation is checked here)
    held = {r["item"]: r["qm"] for r in dk("/api/link/settings", tok=dk("/api/login", {"kind": "owner", "password": "owner123"})["token"])["reserved"]
            if r["order_id"] == o3["id"]}
    ok(held == {"Tata Salt 1kg": 1000, "Aashirvaad Atta 5kg": 2000}, "Dukaan back → the queued order's 1 salt + 2 atta are put aside")

    # ---------- link off: both stand alone
    sk("/api/link/settings", {"enabled": False}, A)
    o4 = sk("/api/orders", {"customer": {"name": "Om", "phone": "9222222222"}, "mode": "pickup", "items": [{"productId": salt["id"], "qty": 1}]}, expect=201)
    time.sleep(1)
    otok2 = dk("/api/login", {"kind": "owner", "password": "owner123"})["token"]
    ok(not sk("/api/link/settings", tok=A)["queue"] and not any(r["order_id"] == o4["id"] for r in dk("/api/link/settings", tok=otok2)["reserved"]),
       "StoreKart link off: order stays in StoreKart only")
    dk("/api/link/settings", {"enabled": False}, otok2)
    call(DP, "/api/link/stock", hdr={"X-Link-Key": key}, expect=401)
    ok(dk("/api/hello")["storekart_login"] is False, "Dukaan link off: key refused, no online-store login")
    print("ALL LINK TESTS PASSED")
finally:
    for p in procs.values():
        p.terminate()
    for p in procs.values():
        try:
            p.wait(timeout=10)
        except Exception:
            pass
    shutil.rmtree(d_data, ignore_errors=True)
    shutil.rmtree(s_data, ignore_errors=True)
