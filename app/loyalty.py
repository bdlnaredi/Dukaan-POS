"""Loyalty points, kept by the customer's mobile number.

Points are earned on what the customer pays (after discounts and points used): `per100` points for every
Rs 100. A point is worth `value` paise when used. Using points is a discount on the bill, so GST is on the
lower amount. A return takes back the points its share of the bill earned; a cancelled bill takes back all
it earned and gives back any points it used.
"""
import re
import time

import db

DEFAULTS = {"enabled": False, "per100": 1, "value": 100, "min": 50, "max_pct": 50}
PHONE = re.compile(r"^[6-9]\d{9}$")


def settings():
    s = dict(DEFAULTS)
    s.update(((db.get("settings") or {}).get("loyalty") or {}))
    return s


def earned_for(amount):
    s = settings()
    return int(amount * s["per100"]) // 10000 if s["enabled"] else 0


def get(phone):
    r = db.one("SELECT * FROM loyalty WHERE phone=?", (phone,))
    return r or {"phone": phone, "name": "", "points": 0, "earned": 0, "redeemed": 0}


def _move(c, phone, uid, kind, pts, note="", name=""):
    if not pts:
        return
    now = time.time()
    c.execute("""INSERT INTO loyalty(phone, name, points, earned, redeemed, first, updated) VALUES(?, ?, 0, 0, 0, ?, ?)
        ON CONFLICT(phone) DO NOTHING""", (phone, name, now, now))
    if name:
        c.execute("UPDATE loyalty SET name=? WHERE phone=? AND (name IS NULL OR name='')", (name, phone))
    cur = c.execute("SELECT points FROM loyalty WHERE phone=?", (phone,)).fetchone()["points"]
    new = max(0, cur + pts)            # never below zero
    c.execute("UPDATE loyalty SET points=?, earned=earned+?, redeemed=redeemed+?, updated=? WHERE phone=?",
              (new, pts if kind == "earn" else 0, -pts if kind == "redeem" else 0, now, phone))
    c.execute("INSERT INTO loyalty_log(phone, bill_uid, kind, points, at, note) VALUES(?,?,?,?,?,?)",
              (phone, uid, kind, new - cur, now, note[:200]))


def on_sale(c, uid, number, buyer, calc, redeem):
    """Points for a sale (inside the bill's transaction). redeem: {points, value} the counter applied.
    Returns (earned, flag message or "")."""
    s = settings()
    phone = re.sub(r"\D", "", str(buyer.get("phone") or ""))[-10:]
    if not s["enabled"] or not PHONE.match(phone):
        return 0, ("Points were used but loyalty is off or there's no mobile number" if redeem and redeem.get("points") else "")
    flag = ""
    if redeem and redeem.get("points"):
        pts = int(redeem["points"])
        have = c.execute("SELECT points FROM loyalty WHERE phone=?", (phone,)).fetchone()
        have = have["points"] if have else 0
        if pts > have:
            flag = f"{pts} points were used but {phone} had only {have}"
        if redeem.get("value") != pts * s["value"]:
            flag = (flag + "; " if flag else "") + "Points were valued differently from the loyalty setting"
        _move(c, phone, uid, "redeem", -pts, f"Used on bill {number}", buyer.get("name", ""))
    earned = earned_for(calc["amount"])
    _move(c, phone, uid, "earn", earned, f"Bill {number}", buyer.get("name", ""))
    return earned, flag


def on_cancel(c, uid):
    """Undo everything a bill (or return) did to points."""
    for r in c.execute("SELECT phone, kind, points FROM loyalty_log WHERE bill_uid=? AND kind IN ('earn', 'redeem', 'reverse')", (uid,)).fetchall():
        _move(c, r["phone"], uid, "undo", -r["points"], "Bill cancelled")


def on_return(c, ret_uid, orig_uid, ret_amount, orig_amount):
    """Take back the points the returned part of the bill earned."""
    if not orig_amount:
        return
    for r in c.execute("SELECT phone, points FROM loyalty_log WHERE bill_uid=? AND kind='earn'", (orig_uid,)).fetchall():
        back = r["points"] * ret_amount // orig_amount
        if back:
            _move(c, r["phone"], ret_uid, "reverse", -back, "Items returned")


def adjust(phone, pts, note, who):
    if not PHONE.match(phone):
        raise ValueError("That isn't a 10-digit mobile number.")
    with db.tx() as c:
        _move(c, phone, None, "adjust", int(pts), f"{who}: {note}")


def customers(q=""):
    q = f"%{q}%"
    return db.rows("SELECT * FROM loyalty WHERE phone LIKE ? OR name LIKE ? ORDER BY updated DESC LIMIT 300", (q, q))


def history(phone):
    return db.rows("""SELECT l.*, b.number FROM loyalty_log l LEFT JOIN bill b ON b.uid=l.bill_uid WHERE l.phone=?
        ORDER BY l.id DESC LIMIT 100""", (phone,))
