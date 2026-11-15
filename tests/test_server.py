"""End to end: Dukaan against the fake Tally. Run: python3 tests/test_server.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TP, DP = 19123, 19124
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


def tally_ctl(d):
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{TP}/control", data=json.dumps(d).encode()), timeout=5)


def tally_state():
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{TP}/", timeout=5).read())


def wait_posted(tok, n, secs=30):
    for _ in range(secs * 4):
        st = call("/api/tally", tok=tok)
        if st["counts"].get("posted", 0) >= n:
            return st
        time.sleep(.25)
    raise AssertionError(f"only {st['counts']} posted; {st['state']} {st['failed']}")


try:
    for _ in range(40):
        try:
            call("/api/hello")
            break
        except Exception:
            time.sleep(.25)
    call("/api/login", {"kind": "owner", "password": "nope"}, expect=401)
    tok = call("/api/login", {"kind": "owner", "password": "owner123", "device": "dev-owner"})["token"]

    # connect to Tally and copy masters
    r = call("/api/tally/connect", {"url": f"127.0.0.1:{TP}"}, tok)
    assert r["company"] == "Shree Ganesh Kirana", r
    st = call("/api/tally/refresh", {}, tok)
    assert st["result"]["items"] == 8, st["result"]
    s = st["setup"]
    print("guessed setup:", json.dumps({k: s[k] for k in s if k != "company"}))
    assert s["sales_vtype"] == "Counter Sales" and s["cash"] == "Cash" and s["upi"] == "HDFC Bank UPI", s
    assert s["card"] == "SBI Card Machine" and s["round_off"] == "Round Off" and s["godown"] == "Shop", s
    assert s["cgst"]["5"] == "Output CGST 2.5%" and s["sgst"]["18"] == "Output SGST 9%" and s["sales_ledgers"]["0"] == "Sales Exempt", s
    assert s["sales_ledgers"]["12"] == "Sales GST 12%", s
    assert not st["problems"], st["problems"]
    items = {i["name"]: i for i in call("/api/items", tok=tok)["items"]}
    assert items["Tata Salt 1kg"]["codes"] == ["8904043901015"] and items["Tata Salt 1kg"]["price"] == 2800
    assert items["Moong Dal Loose"]["gst"] == 0 and items["Basmati Rice Loose"]["decimals"] == 3
    assert not items["Mystery Item"]["can_sell"] and "No GST rate in Tally" in items["Mystery Item"]["issues"]

    call("/api/settings", {"shop": {"name": "Shree Ganesh Kirana", "address": "Main Bazaar, Ajeetgarh", "gstin": "08ABCPN1234F1Z5",
                                    "phone": "9876543210"}, "upi": {"vpa": "ganesh@okhdfcbank", "name": "Ganesh Kirana"}}, tok, expect=400)
    call("/api/settings", {"shop": {"name": "Shree Ganesh Kirana", "address": "Main Bazaar, Ajeetgarh", "gstin": "08AAACR5055K1Z7",
                                    "phone": "9876543210"}, "upi": {"vpa": "ganesh@okhdfcbank", "name": "Ganesh Kirana"}}, tok)
    call("/api/cashier", {"name": "Ravi", "pin": "4321", "active": True, "can_discount": True}, tok)
    ctok = call("/api/login", {"kind": "cashier", "name": "Ravi", "pin": "4321", "device": "dev-1"})["token"]
    claim = call("/api/counter/claim", {"counter_id": 1}, ctok)
    assert claim["counter"]["series"] == "A"
    fy = [k for k in claim["numbers"]][0]
    boot = call("/api/boot", tok=ctok)
    assert "Mystery Item" in [i["name"] for i in boot["items"]]

    def bill(n, lines, pays, buyer=None, uid=None, **kw):
        import gst  # noqa
        b = {"uid": uid or f"t{n:04d}-{os.urandom(4).hex()}", "counter_id": 1, "number": f"A/{fy}/{n}", "date": TODAY,
             "time": "10:00:00", "lines": lines, "payments": pays, "buyer": buyer or {}, "round": True, **kw}
        return b

    sys.path.insert(0, os.path.join(ROOT, "app"))
    import gst as G

    def L(name, qty, disc=0):
        i = items[name]
        return {"item": name, "qty": qty, "price": i["price"], "mrp": i["mrp"], "gst": i["gst"], "hsn": i["hsn"], "disc": disc}

    b1 = bill(1, [L("Tata Salt 1kg", "2"), L("Surf Excel 1kg", "1"), L("Basmati Rice Loose", "1.250"), L("Moong Dal Loose", "0.5")], [])
    c1 = G.compute(b1)
    b1["payments"] = [{"mode": "cash", "amount": c1["amount"]}]
    b1["amount"] = c1["amount"]
    # split cash + UPI
    b2 = bill(2, [L("Aashirvaad Atta 5kg", "1"), L("Amul Ghee 1L", "1", disc=1000)], [])
    c2 = G.compute(b2)
    b2.update(amount=c2["amount"], payments=[{"mode": "cash", "amount": 50000}, {"mode": "upi", "amount": c2["amount"] - 50000}])
    # UPI only
    b3 = bill(3, [L("Parle-G 250g", "4")], [])
    c3 = G.compute(b3)
    b3.update(amount=c3["amount"], payments=[{"mode": "upi", "amount": c3["amount"]}])
    r = call("/api/bills", {"bills": [b1, b2, b3]}, ctok)
    assert all(x["status"] == "ok" and not x["flag"] for x in r["results"]), r
    # credit to a customer without permission -> marked for the owner
    b4 = bill(4, [L("Surf Excel 1kg", "2")], [], buyer={"ledger": "Ramesh Kumar"})
    b4["amount"] = G.compute(b4)["amount"]
    r = call("/api/bills", {"bills": [b4]}, ctok)["results"][0]
    assert "isn't allowed to give credit" in r["flag"], r
    # same bill sent again: no duplicate
    r = call("/api/bills", {"bills": [b1]}, ctok)["results"][0]
    assert r.get("again"), r
    # number clash from another bill
    b5 = bill(3, [L("Parle-G 250g", "1")], [])
    b5.update(amount=G.compute(b5)["amount"], payments=[{"mode": "cash", "amount": G.compute(b5)["amount"]}])
    r = call("/api/bills", {"bills": [b5]}, ctok)["results"][0]
    assert "used twice" in r["flag"], r
    # wrong total from the counter
    b6 = bill(6, [L("Parle-G 250g", "1")], [{"mode": "cash", "amount": 2500}], amount=2400)
    r = call("/api/bills", {"bills": [b6]}, ctok)["results"][0]
    assert "isn't what the server works out" in r["flag"], r

    # nothing goes to Tally before the owner confirms the setup
    time.sleep(1.5)
    assert tally_state()["imports"] == 0
    st = call("/api/tally/setup", {"confirmed": True}, tok)
    st = wait_posted(tok, 3)
    vs = tally_state()["vouchers"]
    kinds = sorted((v["vtype"], v["number"]) for v in vs)
    print("in Tally:", kinds)
    assert ("Counter Sales", f"A/{fy}/1") in kinds and ("Contra", "1") in kinds and len(vs) == 4, kinds
    sale1 = next(v for v in vs if v["number"] == f"A/{fy}/1")
    assert "<PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>" in sale1["xml"] and "Round Off" in sale1["xml"] or c1["round_off"] == 0
    sale2 = next(v for v in vs if v["number"] == f"A/{fy}/2")
    assert "<PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>" in sale2["xml"]
    sale3 = next(v for v in vs if v["number"] == f"A/{fy}/3")
    assert "<PARTYLEDGERNAME>HDFC Bank UPI</PARTYLEDGERNAME>" in sale3["xml"]

    # owner approves the credit bill -> sales voucher to Ramesh (bill-wise)
    call(f"/api/bill/{b4['uid']}/approve", {}, tok)
    wait_posted(tok, 4)
    v4 = [v for v in tally_state()["vouchers"] if v["number"] == f"A/{fy}/4"][0]
    assert "New Ref" in v4["xml"] and "Ramesh Kumar" in v4["xml"]

    # credit with part paid, by the owner: sale + receipt
    b7 = bill(7, [L("Amul Ghee 1L", "2")], [], buyer={"ledger": "Sharma Tea Stall", "gstin": "08AAACR5055K1Z7"})
    c7 = G.compute(b7)
    b7.update(amount=c7["amount"], payments=[{"mode": "cash", "amount": 20000}])
    otok = call("/api/login", {"kind": "owner", "password": "owner123", "device": "dev-1"})["token"]
    r = call("/api/bills", {"bills": [b7]}, otok)["results"][0]
    assert not r["flag"], r
    wait_posted(tok, 5)
    vs = tally_state()["vouchers"]
    assert any(v["vtype"] == "Receipt" and "Agst Ref" in v["xml"] for v in vs), [v["vtype"] for v in vs]
    assert "<PARTYGSTIN>08AAACR5055K1Z7</PARTYGSTIN>" in [v for v in vs if v["number"] == f"A/{fy}/7"][0]["xml"]

    # Tally refuses one: shown as failed, retried after the fix
    tally_ctl({"fail_next": "Ledger 'X' does not exist!"})
    b8 = bill(8, [L("Tata Salt 1kg", "1")], [{"mode": "card", "amount": 2800}], amount=2800)
    call("/api/bills", {"bills": [b8]}, ctok)
    for _ in range(80):
        st = call("/api/tally", tok=tok)
        if st["failed"]:
            break
        time.sleep(.25)
    assert "does not exist" in st["failed"][0]["tally_error"], st["failed"]
    call("/api/tally/retry", {}, tok)
    wait_posted(tok, 6)

    # Tally adds it but the answer is lost: no second copy
    tally_ctl({"drop_after_create": 1})
    b9 = bill(9, [L("Parle-G 250g", "2")], [{"mode": "cash", "amount": 5000}], amount=5000)
    call("/api/bills", {"bills": [b9]}, ctok)
    wait_posted(tok, 7, 60)
    n9 = [v for v in tally_state()["vouchers"] if v["number"] == f"A/{fy}/9"]
    assert len(n9) == 1, n9

    # a return against bill 1 (needs permission)
    call("/api/return/preview", {"orig_uid": b1["uid"], "lines": [{"index": 0, "qty": "1"}]}, ctok)
    call("/api/return", {"uid": "ret-0001-abcd", "orig_uid": b1["uid"], "lines": [{"index": 0, "qty": "1"}], "refund": "cash"},
         ctok, expect=403)
    r = call("/api/return", {"uid": "ret-0001-abcd", "orig_uid": b1["uid"], "counter_id": 1,
                             "lines": [{"index": 0, "qty": "1"}, {"index": 2, "qty": "1.25"}], "refund": "cash"}, tok)
    assert r["number"] == f"CNA/{fy}/1", r
    call("/api/return", {"uid": "ret-0002-abcd", "orig_uid": b1["uid"], "lines": [{"index": 0, "qty": "2"}], "refund": "cash"},
         tok, expect=400)          # only 1 left
    wait_posted(tok, 8)
    cn = [v for v in tally_state()["vouchers"] if v["vtype"] == "Credit Note"][0]
    assert f"<REFERENCE>A/{fy}/1</REFERENCE>" in cn["xml"], cn["xml"][:500]

    # cancel: a bill with a return can't be; a flagged one never sent is just cancelled here
    call(f"/api/bill/{b1['uid']}/cancel", {"reason": "x"}, tok, expect=400)
    call(f"/api/bill/{b6['uid']}/cancel", {"reason": "wrong total"}, tok)
    call(f"/api/bill/{b2['uid']}/cancel", {"reason": "x"}, ctok, expect=403)
    # a split bill already in Tally (sales + contra): both cancelled there, nothing deleted
    before = len(tally_state()["vouchers"])
    call(f"/api/bill/{b2['uid']}/cancel", {"reason": "customer changed mind"}, tok)
    for _ in range(80):
        b = call(f"/api/bill/{b2['uid']}", tok=tok)
        if b["tally"] in ("cancelled", "cancel_failed"):
            break
        time.sleep(.25)
    assert b["tally"] == "cancelled", b["tally_error"]
    vs = tally_state()["vouchers"]
    assert len(vs) == before
    mine = [v for v in vs if b2["uid"] in v["narration"]]
    assert len(mine) == 2 and all(v.get("cancelled") for v in mine), mine
    assert all(not v.get("cancelled") for v in vs if b2["uid"] not in v["narration"])

    # reports
    s = call(f"/api/reports/summary?from={TODAY}&to={TODAY}", tok=tok)
    print("summary:", {k: s[k] for k in ("sales", "bills", "returns", "net", "modes", "cash_in_hand", "not_in_tally", "flagged")})
    docs = call(f"/api/reports/docs?from={TODAY}&to={TODAY}", tok=tok)["rows"]
    print("docs:", docs)
    h = call(f"/api/reports/hsn?from={TODAY}&to={TODAY}", tok=tok)["rows"]
    tot = sum(r["value"] for r in h)
    assert tot == s["net"], (tot, s["net"])
    assert sum(r["taxable"] + r["cgst"] + r["sgst"] for r in h) == tot
    csv = call(f"/api/reports/bills.csv?from={TODAY}&to={TODAY}", tok=tok)
    assert csv.count("\n") >= 9
    page = urllib.request.urlopen(f"http://127.0.0.1:{DP}/b/" + call(f"/api/bill/{b2['uid']}", tok=tok)["link"]).read().decode()
    assert "TAX INVOICE" in page and "Place of supply" in page and "GSTIN" in page
    assert "<svg" in call("/api/qr?amount=12300&note=A1", tok=ctok)["svg"]
    print("ALL SERVER TESTS PASSED")
finally:
    srv.terminate()
    ft.terminate()
    shutil.rmtree(data, ignore_errors=True)
