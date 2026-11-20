"""Link with StoreKart, the online store. Off until the owner turns it on in Settings > Online store link.

When it's on, StoreKart talks to Dukaan with a secret link key (sent as the X-Link-Key header):
  - it reads the items and their live stock, so the website shows what's really in the shop;
  - when an online order is placed it puts that stock aside (a reservation), so the counter can't sell it twice;
  - when the order is completed and paid it becomes a Dukaan bill in its own series (W/2627/1 ...), which goes
    into Tally like any counter bill (and earns loyalty points on the customer's mobile number);
  - a cancelled order gives its stock back;
  - it can check a cashier's name and PIN, so Dukaan cashiers can sign in to StoreKart too.
And StoreKart's owner, admin, manager and cashier can sign in to Dukaan with their StoreKart login.

With the link off (or StoreKart unreachable) Dukaan works exactly as it does on its own.
"""
import hmac
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import date

import auth
import billing
import billpage
import db
import gst as G
import poster
import stock

settings_fn = None   # main.settings, set by main.py

ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
_sale_lock = threading.Lock()


# ---------------------------------------------------------------- settings

def cfg():
    c = db.get("link") or {}
    c.setdefault("enabled", False)
    c.setdefault("key", "")
    c.setdefault("storekart_url", "")
    c.setdefault("series", "W")
    c.setdefault("staff_login", True)
    return c


def public_cfg():
    c = cfg()
    out = {k: c[k] for k in ("enabled", "key", "storekart_url", "series", "staff_login")}
    out["last_seen"] = c.get("last_seen", 0)
    out["online_counter"] = (online_counter(create=False) or {}).get("name", "")
    out["reserved"] = db.rows("""SELECT order_id, number, item, qm, created FROM link_reserve ORDER BY created DESC LIMIT 200""")
    return out


def save_cfg(d, who):
    c = cfg()
    if d.get("new_key") or (d.get("enabled") and not c["key"]):
        c["key"] = "dk_" + secrets.token_urlsafe(24)
    url = str(d.get("storekart_url", c["storekart_url"]) or "").strip()
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    url = url.split("#")[0]
    if url and not url.endswith("/"):
        url += "/"
    c["storekart_url"] = url
    series = str(d.get("series", c["series"]) or "W").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,3}", series):
        raise ValueError("Series is 1 to 3 letters or digits.")
    clash = db.one("SELECT name FROM counter WHERE series=? AND name<>'Online store'", (series,))
    if clash:
        raise ValueError(f"Series {series} is already used by {clash['name']}.")
    c["series"] = series
    c["enabled"] = bool(d.get("enabled", c["enabled"]))
    c["staff_login"] = bool(d.get("staff_login", c["staff_login"]))
    db.put("link", c)
    db.log(who, "Online store link " + ("on" if c["enabled"] else "off"))
    if c["enabled"]:
        online_counter()
    return public_cfg()


def key_ok(key):
    c = cfg()
    ok = bool(c["enabled"] and c["key"] and key and hmac.compare_digest(str(key), c["key"]))
    if ok and time.time() - c.get("last_seen", 0) > 60:
        c["last_seen"] = time.time()
        db.put("link", c)
    return ok


def online_counter(create=True):
    """The counter online bills are numbered in. It's not offered to billing devices (active=0)."""
    series = cfg()["series"]
    row = db.one("SELECT * FROM counter WHERE name='Online store'")
    if row and row["series"] != series and create:
        with db.tx() as c:
            c.execute("UPDATE counter SET series=? WHERE id=?", (series, row["id"]))
        row = db.one("SELECT * FROM counter WHERE id=?", (row["id"],))
    if not row and create:
        with db.tx() as c:
            c.execute("INSERT INTO counter(name, series, active) VALUES('Online store', ?, 0)", (series,))
        row = db.one("SELECT * FROM counter WHERE name='Online store'")
    return row


# ---------------------------------------------------------------- calls from StoreKart (X-Link-Key)

def api(method, path, q, body, ip):
    if path == "/api/link/hello":
        return {"app": "dukaan", "shop": billpage.shop().get("name", ""), "series": cfg()["series"],
                "items": len(db.rows("SELECT name FROM item"))}
    if path == "/api/link/items":
        return {"items": items()}
    if path == "/api/link/stock":
        live = stock.live()
        return {"stock": live, "at": time.time()}
    if path == "/api/link/reserve" and method == "POST":
        return reserve(body)
    if path == "/api/link/release" and method == "POST":
        return release(str(body.get("order_id") or ""))
    if path == "/api/link/sale" and method == "POST":
        return sale(body)
    if path == "/api/link/cancel" and method == "POST":
        return cancel_sale(body)
    if path == "/api/link/verify" and method == "POST":
        return verify(body, ip)
    if path == "/api/link/cashiers":
        return {"cashiers": [{"id": c["id"], "name": c["name"], "active": bool(c["active"])} for c in auth.cashiers()]}
    raise ValueError("Not found")


def items():
    out = []
    for i in billing.items():
        out.append({"name": i["name"], "group": i["group"], "unit": i["unit"], "price": i["price"], "mrp": i["mrp"],
                    "gst": i["gst"], "hsn": i["hsn"], "stock": i["stock"], "codes": i["codes"], "loose": i["loose"],
                    "can_sell": i["can_sell"]})
    return out


def _lines(src):
    """[{item, qty}] -> [(item, thousandths)] for items Dukaan has."""
    have = {r["name"] for r in db.rows("SELECT name FROM item")}
    out = {}
    for x in (src or [])[:300]:
        name = str(x.get("item") or "")
        if name not in have:
            raise ValueError(f"{name} isn't an item in Dukaan.")
        out[name] = out.get(name, 0) + G.milli(_qty(x.get("qty")))
    return out


def _qty(v):
    """3, 2.5, '0.250' -> '0.25' style text with at most 3 decimals."""
    f = round(float(v), 3)
    if f <= 0:
        raise ValueError("Quantity must be more than zero.")
    return ("%.3f" % f).rstrip("0").rstrip(".")


def reserve(d):
    oid = str(d.get("order_id") or "")
    if not ORDER_ID.match(oid):
        raise ValueError("Order id isn't right.")
    if db.one("SELECT 1 x FROM bill WHERE uid=?", ("sk-" + oid,)):
        return {"ok": True, "already_billed": True}
    lines = _lines(d.get("lines"))
    with db.tx() as c:
        c.execute("DELETE FROM link_reserve WHERE order_id=?", (oid,))
        for item, qm in lines.items():
            c.execute("INSERT INTO link_reserve(order_id, number, item, qm, created) VALUES(?,?,?,?,?)",
                      (oid, str(d.get("number") or "")[:20], item, qm, time.time()))
    live = stock.live()
    return {"ok": True, "stock": {k: live.get(k) for k in lines}}


def release(oid):
    with db.tx() as c:
        n = c.execute("DELETE FROM link_reserve WHERE order_id=?", (oid,)).rowcount
    return {"ok": True, "released": n}


def sale(d):
    """A finished, paid online order -> a Dukaan bill (then into Tally). Sending it again is fine."""
    oid = str(d.get("order_id") or "")
    if not ORDER_ID.match(oid):
        raise ValueError("Order id isn't right.")
    uid = "sk-" + oid
    with _sale_lock:
        old = db.one("SELECT uid, number, status, flag, link FROM bill WHERE uid=?", (uid,))
        if old:
            release(oid)
            return {"uid": uid, "number": old["number"], "status": old["status"], "flag": old["flag"], "again": True, "link": old["link"]}
        imap = billing.item_map()
        lines = []
        for x in (d.get("lines") or [])[:300]:
            name = str(x.get("item") or "")
            it = imap.get(name)
            if not it:
                raise ValueError(f"{name} isn't an item in Dukaan.")
            price = x.get("price")  # paise per unit, GST included (what the customer paid)
            if not isinstance(price, int) or price < 0:
                raise ValueError(f"Price of {name} isn't right.")
            lines.append({"item": name, "qty": _qty(x.get("qty")), "price": price, "mrp": it["mrp"] or 0,
                          "gst": it["gst"], "disc": 0, "hsn": it["hsn"]})
        if not lines:
            raise ValueError("The order has no Dukaan items.")
        settings = settings_fn()
        buyer = d.get("buyer") or {}
        buyer = {"name": str(buyer.get("name") or "")[:120], "phone": re.sub(r"\D", "", str(buyer.get("phone") or ""))[-10:],
                 "address": str(buyer.get("address") or "")[:300], "gstin": "", "ledger": ""}
        try:
            calc = G.compute({"lines": lines, "bill_disc": 0, "round": settings["round"], "buyer": buyer})
            amount = calc["amount"]
        except G.GSTError:
            amount = sum(round(l["price"] * float(l["qty"])) for l in lines)  # billing.accept flags it for the owner
        counter = online_counter()
        day = date.today().isoformat()
        fy = billing.fy_of(day)
        n = billing.last_number(counter["id"], fy) + 1
        mode = "upi" if d.get("payment") == "upi" else "cash"
        payload = {"uid": uid, "date": day, "time": time.strftime("%H:%M:%S"), "counter_id": counter["id"],
                   "number": billing.number(counter["series"], fy, n), "lines": lines, "bill_disc": 0,
                   "round": settings["round"], "buyer": buyer, "amount": amount, "created": time.time(),
                   "payments": [{"mode": mode, "amount": amount, "ref": ("Online #" + str(d.get("number") or ""))[:40]}]}
        sess = {"role": "owner", "name": "Online store", "cashier_id": None, "device": "", "can_credit": True}
        res = billing.accept(payload, sess)
    release(oid)
    poster.wake()
    return res


def cancel_sale(d):
    """An online order cancelled after it became a bill: cancel the bill (and its Tally vouchers)."""
    oid = str(d.get("order_id") or "")
    release(oid)
    b = db.one("SELECT uid, status FROM bill WHERE uid=?", ("sk-" + oid,))
    if not b or b["status"] != "ok":
        return {"ok": True, "cancelled": False}
    billing.cancel(b["uid"], {"role": "owner", "name": "Online store"}, str(d.get("reason") or "Online order cancelled")[:200])
    poster.wake()
    return {"ok": True, "cancelled": True}


def verify(d, ip):
    """StoreKart asks: is this a Dukaan cashier's name and PIN (or the owner's password)?"""
    if not cfg()["staff_login"]:
        raise PermissionError("Signing in with Dukaan logins is switched off in Dukaan.")
    name = str(d.get("name") or "").strip()
    secret = str(d.get("pin") or d.get("password") or "")
    key = "link:" + ip + ":" + name.lower()
    auth._throttle(key)
    if name.lower() in ("owner", "dukaan owner") and auth.owner_ok(secret):
        return {"role": "owner", "name": "Owner", "id": "owner"}
    c = db.one("SELECT * FROM cashier WHERE lower(name)=lower(?) AND active=1", (name,))
    if c and auth._check(secret, c["pin"]):
        return {"role": "cashier", "name": c["name"], "id": str(c["id"])}
    auth._failed(key)
    raise PermissionError("Name or PIN isn't right.")


# ---------------------------------------------------------------- signing in to Dukaan with a StoreKart login

def call_storekart(path, body, timeout=10):
    c = cfg()
    if not (c["enabled"] and c["storekart_url"] and c["key"]):
        raise PermissionError("The online store isn't connected.")
    req = urllib.request.Request(c["storekart_url"] + path.lstrip("/"), data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Link-Key": c["key"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error")
        except Exception:  # noqa: BLE001
            msg = None
        raise PermissionError(msg or f"The online store said no ({e.code}).")
    except (urllib.error.URLError, OSError):
        raise PermissionError("Can't reach the online store right now. Sign in with your Dukaan name and PIN.")


def login_storekart(d, ip):
    c = cfg()
    if not (c["enabled"] and c["staff_login"]):
        raise PermissionError("Signing in with the online store's logins is off.")
    username = str(d.get("username") or "").strip()
    key = "sk:" + ip
    auth._throttle(key)
    try:
        u = call_storekart("api/link/verify", {"username": username, "password": str(d.get("password") or "")}).get("user")
    except PermissionError:
        auth._failed(key)
        raise
    if not u or u.get("role") not in ("admin", "owner", "manager", "cashier"):
        raise PermissionError("Delivery staff can't use the billing counter.")
    if u["role"] in ("admin", "owner"):
        who = {"cashier_id": None, "role": "owner", "name": u.get("name") or "Owner"}
    else:
        who = {"cashier_id": linked_cashier(u), "role": "cashier", "name": u.get("name")}
    device = re.sub(r"[^A-Za-z0-9-]", "", str(d.get("device") or ""))[:40]
    return auth.start_session(who, device)


def linked_cashier(u):
    """The Dukaan cashier for a StoreKart manager/cashier (made the first time they sign in)."""
    ext = "sk:" + str(u.get("id"))
    mgr = 1 if u.get("role") == "manager" else 0
    row = db.one("SELECT * FROM cashier WHERE ext=?", (ext,))
    with db.tx() as c:
        if row:
            if not row["active"]:
                raise PermissionError("This login is switched off in Dukaan.")
            c.execute("UPDATE cashier SET can_return=MAX(can_return, ?), can_credit=MAX(can_credit, ?) WHERE id=?", (mgr, mgr, row["id"]))
            return row["id"]
        name = re.sub(r"\s+", " ", str(u.get("name") or "Staff")).strip()[:30]
        if c.execute("SELECT 1 FROM cashier WHERE name=?", (name,)).fetchone():
            name = (name + " (online)")[:40]
        # a PIN nobody knows: this cashier signs in through the online store
        c.execute("INSERT INTO cashier(name, pin, role, active, can_return, can_discount, can_credit, created, ext) "
                  "VALUES(?, ?, 'cashier', 1, ?, 1, ?, ?, ?)", (name, auth._hash(secrets.token_hex(16)), mgr, mgr, time.time(), ext))
        cid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    db.log("Online store", f"Cashier {name} added (signs in with the online store login)")
    return cid
