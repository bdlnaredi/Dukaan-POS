"""Dukaan: counter billing (POS) for a shop whose books are in TallyPrime.

Standard library only. Settings from environment variables:
  APP_PASSWORD  the owner's first password (required; change it later in the app)
  DATA_DIR      where the database lives (default /data)
  PORT          default 2510
"""
import gzip
import json
import mimetypes
import os
import re
import sys
import time
import traceback
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth  # noqa: E402
import billing  # noqa: E402
import billpage  # noqa: E402
import db  # noqa: E402
import masters  # noqa: E402
import poster  # noqa: E402
import reports  # noqa: E402
import tally  # noqa: E402
import loyalty  # noqa: E402
import offers  # noqa: E402
import shifts  # noqa: E402
import stock  # noqa: E402
import link  # noqa: E402

VERSION = "0.4.0"
PORT = int(os.environ.get("PORT", "2510"))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
VPA = re.compile(r"^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9.\-]{1,63}$")

DEFAULT_SETTINGS = {
    "shop": {"name": "", "address": "", "phone": "", "gstin": "", "state": "", "fssai": "", "email": ""},
    "prefix": "", "round": True, "hsn_digits": 6, "public_url": "", "tally_url": "", "refresh_minutes": 30,
    "upi": {"vpa": "", "name": ""}, "print": {"width": "80", "footer": "Thank you! Visit again.", "auto": False},
    "stock_warn": True, "stock_block": False, "expiry_days": 30, "ask_opening": True,
    "loyalty": dict(loyalty.DEFAULTS),
}


def settings():
    s = db.get("settings") or {}
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for k, v in s.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


class Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def owner(sess):
    if not sess or sess["role"] != "owner":
        raise Err(403, "Only the owner can do this.")


def _day(q, k, default):
    v = (q.get(k) or [default])[0]
    try:
        return date.fromisoformat(v).isoformat()
    except ValueError:
        raise Err(400, "Pick a date.")


def upi_uri(amount, note):
    u = settings()["upi"]
    if not u.get("vpa"):
        return None
    q = [("pa", u["vpa"]), ("pn", u.get("name") or settings()["shop"].get("name") or "Shop")]
    if amount:
        q.append(("am", f"{amount / 100:.2f}"))
    q.append(("cu", "INR"))
    if note:
        q.append(("tn", note[:60]))
    return "upi://pay?" + "&".join(f"{k}={quote(v, safe='@.-_')}" for k, v in q)


def qr_svg(text):
    import io
    import segno
    out = io.BytesIO()
    segno.make(text, error="m", micro=False).save(out, kind="svg", scale=6, border=3, xmldecl=False, svgns=True)
    return out.getvalue().decode()


# ---------------------------------------------------------------- the API

def api(method, path, q, body, sess, ip):
    s = settings()
    if path == "/api/hello":
        return {"version": VERSION, "shop": s["shop"].get("name") or (db.get("tally_company_info") or {}).get("name", ""),
                "cashiers": [c["name"] for c in auth.cashiers() if c["active"]],
                "owner_set": bool(db.get("owner_pw") or os.environ.get("APP_PASSWORD")),
                "storekart_login": bool(link.cfg()["enabled"] and link.cfg()["staff_login"] and link.cfg()["storekart_url"])}
    if path == "/api/login" and method == "POST":
        try:
            if body.get("kind") == "storekart":
                tok, me = link.login_storekart(body, ip)
            else:
                tok, me = auth.login(body, ip)
        except PermissionError as e:
            raise Err(401, str(e))
        return {"token": tok, "me": _me(me)}
    if not sess:
        raise Err(401, "Please sign in.")
    if path == "/api/logout" and method == "POST":
        auth.logout(sess["token"])
        return {}
    if path == "/api/boot":
        return boot(sess)
    if path == "/api/counter/claim" and method == "POST":
        c = db.one("SELECT * FROM counter WHERE id=? AND active=1", (body.get("counter_id"),))
        if not c:
            raise Err(404, "No such counter.")
        with db.tx() as cx:
            cx.execute("UPDATE counter SET device=?, claimed=? WHERE id=?", (sess["device"], time.time(), c["id"]))
        return {"counter": {"id": c["id"], "name": c["name"], "series": c["series"]},
                "numbers": {fy: billing.last_number(c["id"], fy) for fy in _fys()}}
    if path == "/api/bills" and method == "POST":
        out = []
        for b in (body.get("bills") or [])[:200]:
            try:
                out.append(billing.accept(b, sess))
            except ValueError as e:
                out.append({"uid": b.get("uid"), "error": str(e)})
        if any(r.get("status") == "ok" for r in out):
            poster.wake()
        return {"results": out}
    if path == "/api/bills":
        return {"bills": bill_list(q, sess)}
    m = re.match(r"^/api/bill/([A-Za-z0-9-]+)(?:/(cancel|approve|retry))?$", path)
    if m:
        uid, act = m.group(1), m.group(2)
        if act and method == "POST":
            try:
                if act == "cancel":
                    billing.cancel(uid, sess, body.get("reason", ""))
                    poster.wake()
                elif act == "approve":
                    billing.approve(uid, sess)
                    poster.wake()
                else:
                    owner(sess)
                    poster.retry(uid)
            except PermissionError as e:
                raise Err(403, str(e))
            return {"ok": True}
        return bill_full(uid)
    if path == "/api/return/preview" and method == "POST":
        _, data, lines, calc = billing.return_preview(str(body.get("orig_uid")), body.get("lines") or [])
        return {"lines": lines, "calc": calc, "buyer": data["buyer"]}
    if path == "/api/return" and method == "POST":
        try:
            r = billing.make_return(body, sess)
        except PermissionError as e:
            raise Err(403, str(e))
        poster.wake()
        return r
    if path == "/api/stock":
        return {"stock": stock.live(), "batches": stock.batch_summary(), "at": time.time()}
    if path == "/api/loyalty":
        ph = re.sub(r"\D", "", (q.get("phone") or [""])[0])[-10:]
        if not loyalty.PHONE.match(ph):
            raise Err(400, "That isn't a 10-digit mobile number.")
        return {"customer": loyalty.get(ph), "settings": loyalty.settings()}
    if path.startswith("/api/shift/") or path == "/api/shift":
        cid = int(body.get("counter_id") or (q.get("counter_id") or ["0"])[0] or 0)
        if not db.one("SELECT 1 x FROM counter WHERE id=?", (cid,)):
            raise Err(404, "No such counter.")
        if path == "/api/shift/open" and method == "POST":
            shifts.open_shift(cid, body.get("opening"), sess["name"])
        elif path == "/api/shift/move" and method == "POST":
            shifts.move(cid, body.get("kind"), body.get("amount"), body.get("reason"), sess["name"])
        elif path == "/api/shift/close" and method == "POST":
            done = shifts.close(cid, body.get("denoms") or {}, body.get("kept"), body.get("note"), sess["name"])
            done["summary"] = json.loads(done["summary"])
            done["denoms"] = json.loads(done["denoms"] or "{}")
            return {"closed": done}
        sh = shifts.current(cid)
        last = db.one("SELECT kept, closed_at FROM shift WHERE counter_id=? AND closed_at IS NOT NULL ORDER BY id DESC LIMIT 1", (cid,))
        return {"shift": sh, "summary": shifts.summary(sh) if sh else None, "last_kept": (last or {}).get("kept") or 0,
                "denoms": shifts.DENOMS}
    if path == "/api/qr":
        amount = int((q.get("amount") or ["0"])[0] or 0)
        uri = upi_uri(amount, (q.get("note") or [""])[0])
        if not uri:
            raise Err(404, "Set the shop's UPI ID in Settings.")
        return {"svg": qr_svg(uri), "uri": uri}
    if path.startswith("/api/reports/"):
        if sess["role"] != "owner" and not s.get("cashier_reports"):
            owner(sess)
        today = date.today().isoformat()
        frm, to = _day(q, "from", today), _day(q, "to", today)
        kind = path.rsplit("/", 1)[1]
        if kind == "summary":
            return reports.summary(frm, to)
        if kind == "hsn":
            return {"rows": reports.hsn(frm, to)}
        if kind == "docs":
            return {"rows": reports.docs(frm, to)}
        if kind == "bills.csv":
            return ("text/csv", reports.bills_csv(frm, to), f"dukaan-bills-{frm}-to-{to}.csv")
        if kind == "hsn.csv":
            return ("text/csv", reports.hsn_csv(frm, to), f"dukaan-hsn-{frm}-to-{to}.csv")
        raise Err(404, "No such report.")
    # ------------------------------------------------ owner only from here
    owner(sess)
    if path == "/api/items":
        return {"items": billing.items(include_hidden=True)}
    if path == "/api/item" and method == "POST":
        return save_item(body)
    if path == "/api/batches":
        f = (q.get("filter") or [""])[0]
        item = (q.get("item") or [""])[0] or None
        days = int(settings().get("expiry_days") or 30)
        rows = stock.expiring(days) if f == "expiring" else stock.batches(item, include_empty=f == "all")
        return {"batches": rows, "expiry_days": days}
    if path == "/api/batch" and method == "POST":
        bid = stock.add_batch(body, sess["name"])
        return {"id": bid, "batches": stock.batches(body.get("item"))}
    if path == "/api/batch/remove" and method == "POST":
        stock.remove_batch(int(body.get("id") or 0), sess["name"])
        return {"ok": True}
    if path == "/api/offers":
        return {"offers": offers.all_offers(), "groups": sorted({i["group"] for i in billing.items(include_hidden=True) if i["group"]})}
    if path == "/api/offer" and method == "POST":
        offers.save(body, sess["name"])
        return {"offers": offers.all_offers()}
    if path == "/api/offer/remove" and method == "POST":
        offers.remove(int(body.get("id") or 0), sess["name"])
        return {"offers": offers.all_offers()}
    if path == "/api/loyalty/list":
        return {"customers": loyalty.customers((q.get("q") or [""])[0])}
    if path == "/api/loyalty/history":
        return {"history": loyalty.history(re.sub(r"\D", "", (q.get("phone") or [""])[0])[-10:])}
    if path == "/api/loyalty/adjust" and method == "POST":
        loyalty.adjust(re.sub(r"\D", "", str(body.get("phone") or ""))[-10:], int(body.get("points") or 0), str(body.get("note") or ""), sess["name"])
        return {"ok": True}
    if path == "/api/shifts":
        today = date.today().isoformat()
        return {"shifts": shifts.history(_day(q, "from", today), _day(q, "to", today))}
    if path == "/api/item/newcode" and method == "POST":
        return new_code(str(body.get("name") or ""))
    if path == "/api/settings":
        if method == "POST":
            return save_settings(body)
        return {"settings": s, "counters": db.rows("SELECT * FROM counter ORDER BY id"), "cashiers": auth.cashiers()}
    if path == "/api/counter" and method == "POST":
        return save_counter(body)
    if path == "/api/cashier" and method == "POST":
        auth.save_cashier(body)
        return {"cashiers": auth.cashiers()}
    if path == "/api/password" and method == "POST":
        try:
            auth.set_owner_password(str(body.get("old") or ""), str(body.get("new") or ""))
        except PermissionError as e:
            raise Err(400, str(e))
        return {"ok": True}
    if path == "/api/tally":
        return tally_status()
    if path == "/api/tally/connect" and method == "POST":
        u = str(body.get("url") or "").strip().rstrip("/")
        if u and not re.match(r"^https?://", u):
            u = "http://" + u
        if u and not re.search(r":\d+$", u):
            u += ":9000"
        try:
            names = tally.ping(u)
        except (tally.Unreachable, tally.Unknown) as e:
            raise Err(400, f"{e}. Check that Tally is open, and in Tally: F1 Help > Settings > Connectivity, "
                           "'TallyPrime acts as' is Server or Both, port 9000.")
        st = db.get("settings") or {}
        st["tally_url"] = u
        if body.get("refresh_minutes"):
            st["refresh_minutes"] = max(5, int(body["refresh_minutes"]))
        db.put("settings", st)
        setup = db.get("tally_setup", {}) or {}
        if body.get("company") in names:
            setup["company"] = body["company"]
        elif setup.get("company") not in names:
            setup["company"] = names[0] if names else ""
        db.put("tally_setup", setup)
        return {"companies": names, "company": setup.get("company")}
    if path == "/api/tally/refresh" and method == "POST":
        try:
            r = poster.refresh_now()
        except (tally.Unreachable, tally.Unknown, ValueError) as e:
            raise Err(400, str(e))
        return {"result": r, **tally_status()}
    if path == "/api/tally/setup" and method == "POST":
        cur = db.get("tally_setup", {}) or {}
        for k in ("sales_vtype", "return_vtype", "receipt_vtype", "contra_vtype", "cash", "upi", "card", "round_off", "godown"):
            if k in body:
                cur[k] = str(body[k] or "")
        if "send_batches" in body:
            cur["send_batches"] = bool(body["send_batches"])
        for k in ("sales_ledgers", "cgst", "sgst"):
            if isinstance(body.get(k), dict):
                cur[k] = {str(a): str(b) for a, b in body[k].items()}
        if body.get("confirmed"):
            probs = masters.problems(cur)
            if probs:
                raise Err(400, "Not yet: " + " ".join(probs[:3]))
        cur["confirmed"] = bool(body.get("confirmed"))
        db.put("tally_setup", cur)
        db.log("Owner", "Tally setup " + ("confirmed" if cur["confirmed"] else "saved"))
        poster.retry()
        return tally_status()
    if path == "/api/tally/retry" and method == "POST":
        poster.retry()
        return tally_status()
    if path == "/api/log":
        return {"log": db.rows("SELECT * FROM log ORDER BY id DESC LIMIT 300")}
    if path == "/api/link/settings":
        if method == "POST":
            return link.save_cfg(body, sess["name"])
        return link.public_cfg()
    if path == "/api/link/test" and method == "POST":
        try:
            r = link.call_storekart("api/link/hello", {})
        except PermissionError as e:
            raise Err(400, str(e))
        return {"ok": True, "store": r.get("store", "")}
    raise Err(404, "Not found")


def _fys():
    t = date.today()
    return sorted({billing.fy_of(t.isoformat()), billing.fy_of((t + timedelta(days=40)).isoformat())})


def _me(s):
    return {"name": s["name"], "role": s["role"], "can_return": s["can_return"], "can_discount": s["can_discount"],
            "can_credit": s["can_credit"]}


def boot(sess):
    s = settings()
    shop = billpage.shop()
    setup = db.get("tally_setup", {}) or {}
    pub = {"shop": shop, "round": s["round"], "prefix": s["prefix"], "upi": bool(s["upi"].get("vpa")),
           "upi_vpa": s["upi"].get("vpa", ""), "upi_name": s["upi"].get("name") or shop["name"],
           "print": s["print"], "public_url": s["public_url"].rstrip("/"), "stock_warn": s["stock_warn"],
           "stock_block": s["stock_block"], "ask_opening": s["ask_opening"], "loyalty": s["loyalty"]}
    return {"me": _me(sess), "settings": pub, "items": [i for i in billing.items() if not i["hidden"]],
            "offers": offers.for_counters(),
            "customers": billing.customers(), "counters": db.rows("SELECT id, name, series, device FROM counter WHERE active=1 ORDER BY id"),
            "numbers": {c["id"]: {fy: billing.last_number(c["id"], fy) for fy in _fys()}
                        for c in db.rows("SELECT id FROM counter WHERE active=1")},
            "device": sess["device"], "tally_ready": bool(setup.get("confirmed")), "version": VERSION,
            "refreshed": (db.get("tally_refreshed") or {}).get("at", 0), "now": time.time()}


def bill_list(q, sess):
    today = date.today().isoformat()
    frm, to = _day(q, "from", today), _day(q, "to", today)
    sql = "SELECT uid, kind, number, date, time, cashier, amount, status, flag, tally, tally_error, data FROM bill WHERE date BETWEEN ? AND ?"
    args = [frm, to]
    st = (q.get("state") or [""])[0]
    if st == "flag":
        sql += " AND flag<>'' AND status='ok'"
    elif st == "tally":
        sql += " AND tally NOT IN ('posted', 'skip') AND status='ok'"
    elif st == "failed":
        sql += " AND tally='failed'"
    if sess["role"] != "owner" and not (q.get("all") or [""])[0]:
        sql += " AND cashier=?"
        args.append(sess["name"])
    sql += " ORDER BY created DESC LIMIT 500"
    out = []
    text = (q.get("q") or [""])[0].lower().strip()
    for r in db.rows(sql, args):
        d = json.loads(r.pop("data"))
        r["customer"] = d["buyer"].get("ledger") or d["buyer"].get("name") or ""
        r["phone"] = d["buyer"].get("phone", "")
        r["modes"] = [p["mode"] for p in d.get("payments", [])] if r["kind"] == "sale" else [d.get("refund")]
        if text and text not in (r["number"] + " " + r["customer"] + " " + r["phone"]).lower():
            continue
        out.append(r)
    return out


def bill_full(uid):
    b = db.one("SELECT * FROM bill WHERE uid=?", (uid,))
    if not b:
        raise Err(404, "No such bill.")
    b["data"], b["calc"] = json.loads(b["data"]), json.loads(b["calc"] or "null")
    b["posts"] = db.rows("SELECT seq, kind, label, status, error, tries, done FROM post WHERE bill_uid=? ORDER BY seq", (uid,))
    b["returns"] = db.rows("SELECT uid, number, date, amount, status FROM bill WHERE orig_uid=?", (uid,))
    if b["kind"] == "sale":
        done = billing.returned_so_far(uid)
        b["returnable"] = [l["qm"] - done.get(i, (0, 0))[0] for i, l in enumerate((b["calc"] or {}).get("lines", []))]
    return b


def save_item(d):
    name = str(d.get("name") or "")
    if not db.one("SELECT 1 x FROM item WHERE name=?", (name,)):
        raise Err(404, "No such item.")

    def p(k):
        v = d.get(k)
        if v in (None, ""):
            return None
        if not isinstance(v, int) or v < 0:
            raise Err(400, f"{k} isn't right.")
        return v
    price, mrp = p("price"), p("mrp")
    codes = []
    for c in d.get("codes") or []:
        c = str(c).strip()
        if c and not billing.BARCODE.match(c):
            raise Err(400, f"{c} doesn't look like a barcode.")
        if c:
            codes.append(c)
    for c in codes:
        for other in billing.items(include_hidden=True):
            if other["name"] != name and c in other["codes"]:
                raise Err(400, f"Barcode {c} is already on {other['name']}.")
    g = d.get("gst")
    if g not in (None, ""):
        try:
            g = float(g)
        except ValueError:
            raise Err(400, "GST rate isn't right.")
        if g not in billing.G.SLABS:
            raise Err(400, f"{g:g}% isn't a GST rate.")
    else:
        g = None
    hsn = re.sub(r"\s", "", str(d.get("hsn") or ""))[:10] or None
    if hsn and not re.fullmatch(r"\d{4,8}", hsn):
        raise Err(400, "An HSN code is 4 to 8 digits.")
    ms = d.get("min_stock")
    if ms in (None, ""):
        ms = None
    else:
        try:
            ms = float(ms)
        except (TypeError, ValueError):
            raise Err(400, "The reorder level isn't a number.")
        if ms < 0:
            raise Err(400, "The reorder level can't be below zero.")
    with db.tx() as c:
        c.execute("""INSERT INTO item_local(name, price, mrp, barcodes, loose, fav, hsn, gst, hidden, min_stock, updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET price=excluded.price, mrp=excluded.mrp, barcodes=excluded.barcodes, loose=excluded.loose,
            fav=excluded.fav, hsn=excluded.hsn, gst=excluded.gst, hidden=excluded.hidden, min_stock=excluded.min_stock,
            updated=excluded.updated""",
                  (name, price, mrp, json.dumps(codes), 1 if d.get("loose") else 0, 1 if d.get("fav") else 0, hsn, g,
                   1 if d.get("hidden") else 0, ms, time.time()))
    if g is not None or hsn:
        db.log("Owner", f"Set {name} in Dukaan: HSN {hsn or '-'}, GST {g if g is not None else '-'} (not changed in Tally)")
    return {"item": next(i for i in billing.items(include_hidden=True) if i["name"] == name)}


def _ean_check(d12):
    t = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(d12))
    return str((10 - t % 10) % 10)


def new_code(name):
    """Give an item a shop barcode: an EAN-13 starting 21 (the range set aside for codes used inside one shop)."""
    it = next((i for i in billing.items(include_hidden=True) if i["name"] == name), None)
    if not it:
        raise Err(404, "No such item.")
    taken = {c for i in billing.items(include_hidden=True) for c in i["codes"]}
    with db.write_lock:
        n = int(db.get("next_code") or 1)
        while True:
            d12 = "21" + f"{n:010d}"
            code = d12 + _ean_check(d12)
            n += 1
            if code not in taken:
                break
        db.put("next_code", n)
        loc = db.one("SELECT * FROM item_local WHERE name=?", (name,)) or {}
        codes = json.loads(loc.get("barcodes") or "[]") + [code]
        with db.tx() as c:
            c.execute("""INSERT INTO item_local(name, barcodes, updated) VALUES(?,?,?)
                ON CONFLICT(name) DO UPDATE SET barcodes=excluded.barcodes, updated=excluded.updated""",
                      (name, json.dumps(codes), time.time()))
    return {"code": code, "item": next(i for i in billing.items(include_hidden=True) if i["name"] == name)}


def save_settings(d):
    cur = db.get("settings") or {}
    shop = d.get("shop")
    if isinstance(shop, dict):
        sh = {k: re.sub(r"[ \t]+", " ", str(shop.get(k) or "")).strip()[:300] for k in DEFAULT_SETTINGS["shop"]}
        sh["gstin"] = sh["gstin"].upper().replace(" ", "")
        if sh["gstin"] and not billing.G.gstin_ok(sh["gstin"]):
            raise Err(400, "The shop's GSTIN isn't right. Check it.")
        if sh["gstin"] and not sh["state"]:
            sh["state"] = billing.G.STATE_CODES.get(sh["gstin"][:2], "")
        cur["shop"] = sh
    if "prefix" in d:
        pre = str(d["prefix"] or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9/-]{0,5}", pre):
            raise Err(400, "The bill number prefix can have up to 5 letters, digits, / or -.")
        if db.one("SELECT 1 x FROM bill LIMIT 1") and pre != cur.get("prefix", ""):
            raise Err(400, "Bills have already been made, so the number prefix can't change now (GST needs one series a year).")
        cur["prefix"] = pre
    if "round" in d:
        cur["round"] = bool(d["round"])
    if "hsn_digits" in d:
        cur["hsn_digits"] = 4 if int(d["hsn_digits"] or 6) == 4 else 6
    if "public_url" in d:
        u = str(d["public_url"] or "").strip().rstrip("/")
        if u and not re.match(r"^https?://[^\s]+$", u):
            raise Err(400, "The public address starts with https://")
        cur["public_url"] = u
    if isinstance(d.get("upi"), dict):
        vpa = str(d["upi"].get("vpa") or "").strip()
        if vpa and not VPA.match(vpa):
            raise Err(400, "That isn't a UPI ID. It looks like name@bank, e.g. shop@okhdfcbank.")
        cur["upi"] = {"vpa": vpa, "name": str(d["upi"].get("name") or "").strip()[:50]}
    if isinstance(d.get("print"), dict):
        w = str(d["print"].get("width") or "80")
        cur["print"] = {"width": w if w in ("58", "80", "a4") else "80",
                        "footer": str(d["print"].get("footer") or "")[:300], "auto": bool(d["print"].get("auto"))}
    if "cashier_reports" in d:
        cur["cashier_reports"] = bool(d["cashier_reports"])
    for k in ("stock_warn", "stock_block", "ask_opening"):
        if k in d:
            cur[k] = bool(d[k])
    if "expiry_days" in d:
        cur["expiry_days"] = max(1, min(365, int(d["expiry_days"] or 30)))
    if isinstance(d.get("loyalty"), dict):
        L = d["loyalty"]
        try:
            lo = {"enabled": bool(L.get("enabled")), "per100": float(L.get("per100") or 0), "value": int(L.get("value") or 0),
                  "min": int(L.get("min") or 0), "max_pct": int(L.get("max_pct") or 0)}
        except (TypeError, ValueError):
            raise Err(400, "Check the loyalty numbers.")
        if lo["enabled"] and (not 0 < lo["per100"] <= 100 or not 0 < lo["value"] <= 100000 or not 0 < lo["max_pct"] <= 100):
            raise Err(400, "Check the loyalty numbers: points per Rs 100, value of a point, and the most of a bill points can pay.")
        cur["loyalty"] = lo
    db.put("settings", cur)
    return {"settings": settings()}


def save_counter(d):
    name = str(d.get("name") or "").strip()[:30]
    series = str(d.get("series") or "").strip().upper()
    if not name:
        raise Err(400, "Name the counter, e.g. Counter 1.")
    if not re.fullmatch(r"[A-Z0-9]{1,3}", series):
        raise Err(400, "The series is 1 to 3 letters or digits, e.g. A.")
    test = billing.number(series, "2627", 999999, "return")
    if len(test) > billing.MAX_NUMBER:
        raise Err(400, "With this series and prefix, bill numbers could pass 16 characters. Use a shorter series.")
    with db.tx() as c:
        if d.get("id"):
            old = c.execute("SELECT series FROM counter WHERE id=?", (d["id"],)).fetchone()
            if old and old["series"] != series and c.execute("SELECT 1 FROM bill WHERE counter_id=?", (d["id"],)).fetchone():
                raise Err(400, "This counter has bills, so its series can't change.")
            c.execute("UPDATE counter SET name=?, series=?, active=? WHERE id=?", (name, series, 1 if d.get("active", True) else 0, d["id"]))
        else:
            if c.execute("SELECT 1 FROM counter WHERE series=?", (series,)).fetchone():
                raise Err(400, "Another counter already uses that series.")
            c.execute("INSERT INTO counter(name, series, active) VALUES(?, ?, 1)", (name, series))
    return {"counters": db.rows("SELECT * FROM counter ORDER BY id")}


def tally_status():
    setup = db.get("tally_setup", {}) or {}
    counts = {r["tally"]: r["n"] for r in db.rows("SELECT tally, COUNT(*) n FROM bill WHERE status='ok' OR tally LIKE 'cancel%' GROUP BY tally")}
    failed = db.rows("""SELECT uid, number, date, tally_error FROM bill WHERE (tally='failed' AND status='ok') OR tally='cancel_failed'
        ORDER BY created DESC LIMIT 50""")
    flagged = db.rows("SELECT uid, number, date, flag FROM bill WHERE flag<>'' AND status='ok' ORDER BY created DESC LIMIT 50")
    return {"url": settings()["tally_url"], "refresh_minutes": settings()["refresh_minutes"], "setup": setup,
            "choices": masters.choices(), "problems": masters.problems(setup), "state": poster.state,
            "refreshed": db.get("tally_refreshed") or {}, "counts": counts, "failed": failed, "flagged": flagged,
            "company_info": db.get("tally_company_info") or {}}


# ---------------------------------------------------------------- HTTP

class H(BaseHTTPRequestHandler):
    server_version = "Dukaan"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        if os.environ.get("DEBUG"):
            super().log_message(fmt, *a)

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode()
        gz = len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or "") and not ctype.startswith("image/png")
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8", {"Cache-Control": "no-store"})

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")

    def _go(self, method):
        u = urlparse(self.path)
        path = u.path
        try:
            if path.startswith("/api/"):
                body = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 20 * 1024 * 1024:
                        raise Err(413, "Too much data at once.")
                    raw = self.rfile.read(n) if n else b"{}"
                    body = json.loads(raw or b"{}")
                if path.startswith("/api/link/") and path not in ("/api/link/settings", "/api/link/test"):
                    ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
                    if not link.key_ok(self.headers.get("X-Link-Key")):
                        raise Err(401, "The link key isn't right, or the online store link is off in Dukaan.")
                    try:
                        return self._json(200, link.api(method, path, parse_qs(u.query), body, ip))
                    except PermissionError as e:
                        raise Err(401 if path == "/api/link/verify" else 403, str(e))
                tok = self.headers.get("X-Dukaan-Token") or ""
                sess = auth.session(tok)
                if sess:
                    sess["token"] = tok
                ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
                res = api(method, path, parse_qs(u.query), body, sess, ip)
                if isinstance(res, tuple):
                    ctype, text, fname = res
                    return self._send(200, text, ctype + "; charset=utf-8",
                                      {"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"})
                return self._json(200, res)
            m = re.match(r"^/b/([A-Za-z0-9_-]{8,20})$", path)
            if m:
                b = db.one("SELECT * FROM bill WHERE link=?", (m.group(1),))
                if not b:
                    return self._send(404, "This bill link isn't valid.", "text/plain; charset=utf-8")
                return self._send(200, billpage.render(b), "text/html; charset=utf-8", {"Cache-Control": "no-store",
                                                                                         "X-Robots-Tag": "noindex"})
            return self._static(path)
        except Err as e:
            return self._json(e.code, {"error": str(e)})
        except (ValueError, KeyError) as e:
            if isinstance(e, json.JSONDecodeError):
                return self._json(400, {"error": "That request wasn't readable."})
            return self._json(400, {"error": str(e).strip("'")})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": "Something went wrong on the server. The details are in the container log."})

    def _static(self, path):
        if path in ("/", "") or not os.path.splitext(path)[1]:
            path = "/index.html"
        f = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        if not f.startswith(STATIC + os.sep) or not os.path.isfile(f):
            return self._send(404, "Not found", "text/plain")
        ctype = mimetypes.guess_type(f)[0] or "application/octet-stream"
        if f.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/manifest+json", "image/svg+xml"):
            ctype += "; charset=utf-8"
        with open(f, "rb") as fh:
            data = fh.read()
        if path in ("/index.html", "/sw.js"):
            data = data.replace(b"__VERSION__", VERSION.encode())
        self._send(200, data, ctype, {"Cache-Control": "no-cache"})


link.settings_fn = settings


def main():
    if not os.environ.get("APP_PASSWORD") and not os.path.exists(db.DB_PATH):
        print("Set APP_PASSWORD (the owner's password) for the container.", file=sys.stderr)
        sys.exit(1)
    db.init()
    if not db.rows("SELECT 1 FROM counter"):
        with db.tx() as c:
            c.execute("INSERT INTO counter(name, series, active) VALUES('Counter 1', 'A', 1)")
    poster.start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    srv.daemon_threads = True
    print(f"Dukaan {VERSION} on port {PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
