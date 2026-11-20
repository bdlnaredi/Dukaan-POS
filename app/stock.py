"""Live stock, and stock by batch with expiry dates.

Live stock = Tally's closing stock when Dukaan last copied it, less what was sold (plus what came back)
in Dukaan bills that weren't in Tally at that moment. Bills already in Tally when it was copied are in
Tally's figure, so they aren't taken off twice.

Batches are Dukaan's own record (batch number, MFD, EXP, quantity received). Sales take from the batch
that expires first (and isn't expired yet); returns go back to the batch they came from.
"""
import json
import time
from datetime import date

import db
import gst as G


def _sent_at():
    """{bill uid: when its bill voucher (sales or credit note) went into Tally}"""
    return {r["bill_uid"]: r["done"] for r in db.rows(
        "SELECT bill_uid, done FROM post WHERE seq=1 AND status IN ('done', 'cancelled') AND done IS NOT NULL")}


def adjustments():
    """{item: thousandths to add to Tally's copied stock} for bills Tally's figure doesn't reflect yet."""
    snap = (db.get("tally_refreshed") or {}).get("at", 0)
    sent = _sent_at()
    out = {}
    rows = db.rows("""SELECT uid, kind, status, calc, cancelled_at FROM bill WHERE calc IS NOT NULL AND kind IN ('sale', 'return')
        AND (created > ? OR received > ? OR cancelled_at > ? OR uid NOT IN (SELECT bill_uid FROM post WHERE seq=1 AND status='done'))""",
                   (snap - 86400 * 3, snap - 86400 * 3, snap))
    for b in rows:
        t = sent.get(b["uid"])
        in_tally_copy = bool(t and t <= snap and not (b["cancelled_at"] and b["cancelled_at"] <= snap))
        counts_now = b["status"] == "ok"
        if in_tally_copy == counts_now:
            continue
        sign = (-1 if b["kind"] == "sale" else 1) * (1 if counts_now else -1)
        for l in json.loads(b["calc"])["lines"]:
            out[l["item"]] = out.get(l["item"], 0) + sign * l["qm"]
    return out


def reserved():
    """{item: thousandths} put aside for online orders that aren't bills yet (see link.py)."""
    return {r["item"]: r["q"] for r in db.rows("SELECT item, SUM(qm) q FROM link_reserve GROUP BY item")}


def live():
    """{item: live stock (in units)} for every item copied from Tally, less what online orders have put aside."""
    adj = adjustments()
    res = reserved()
    return {r["name"]: round((r["stock"] or 0) + (adj.get(r["name"], 0) - res.get(r["name"], 0)) / 1000, 3)
            for r in db.rows("SELECT name, stock FROM item")}


# ---------------------------------------------------------------- batches

def batches(item=None, include_empty=False):
    """Batches with what's left, soonest expiry first."""
    sql = """SELECT b.*, b.qm - COALESCE((SELECT SUM(u.qm) FROM batch_use u WHERE u.batch_id=b.id), 0) AS left_qm
        FROM batch b WHERE b.active=1""" + (" AND b.item=?" if item else "")
    rows = db.rows(sql + " ORDER BY COALESCE(b.exp, '9999-12-31'), b.mfd, b.id", (item,) if item else ())
    today = date.today().isoformat()
    out = []
    for r in rows:
        if r["left_qm"] <= 0 and not include_empty:
            continue
        days = (date.fromisoformat(r["exp"]) - date.today()).days if r["exp"] else None
        out.append({"id": r["id"], "item": r["item"], "no": r["batch_no"] or "", "mfd": r["mfd"] or "", "exp": r["exp"] or "",
                    "qty": G.qty(r["qm"]), "left": G.qty(max(r["left_qm"], 0)), "left_qm": r["left_qm"], "mrp": r["mrp"] or 0,
                    "note": r["note"] or "", "expired": bool(r["exp"] and r["exp"] < today), "days": days,
                    "added": r["added"], "added_by": r["added_by"] or ""})
    return out


def batch_summary():
    """{item: [{id, no, exp, left}]} of batches with stock, for the counters (soonest expiry first)."""
    out = {}
    for b in batches():
        out.setdefault(b["item"], []).append({"id": b["id"], "no": b["no"], "exp": b["exp"], "left": b["left_qm"],
                                               "expired": b["expired"]})
    return out


def add_batch(d, who):
    item = str(d.get("item") or "")
    if not db.one("SELECT 1 x FROM item WHERE name=?", (item,)):
        raise ValueError("No such item.")
    qm = G.milli(d.get("qty"))
    if qm <= 0:
        raise ValueError("Enter how much came in.")
    mfd, exp = (str(d.get("mfd") or "") or None), (str(d.get("exp") or "") or None)
    for x, label in ((mfd, "MFD"), (exp, "EXP")):
        if x:
            try:
                date.fromisoformat(x)
            except ValueError:
                raise ValueError(f"{label} isn't a date.")
    if mfd and exp and exp < mfd:
        raise ValueError("EXP is before MFD.")
    mrp = d.get("mrp") or 0
    if not isinstance(mrp, int) or mrp < 0:
        raise ValueError("MRP isn't right.")
    no = str(d.get("no") or "").strip()[:30]
    with db.tx() as c:
        if d.get("id"):
            used = c.execute("SELECT COALESCE(SUM(qm), 0) s FROM batch_use WHERE batch_id=?", (d["id"],)).fetchone()["s"]
            if qm < used:
                raise ValueError(f"{G.qty(used)} of this batch is already sold, so it can't be less than that.")
            c.execute("UPDATE batch SET batch_no=?, mfd=?, exp=?, qm=?, mrp=?, note=? WHERE id=?",
                      (no, mfd, exp, qm, mrp, str(d.get("note") or "")[:200], d["id"]))
            bid = d["id"]
        else:
            c.execute("INSERT INTO batch(item, batch_no, mfd, exp, qm, mrp, note, added, added_by, active) VALUES(?,?,?,?,?,?,?,?,?,1)",
                      (item, no, mfd, exp, qm, mrp, str(d.get("note") or "")[:200], time.time(), who))
            bid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    db.log(who, f"Batch {no or bid} of {item}: {G.qty(qm)}, MFD {mfd or '-'}, EXP {exp or '-'}")
    return bid


def remove_batch(bid, who):
    with db.tx() as c:
        c.execute("UPDATE batch SET active=0 WHERE id=?", (bid,))
    db.log(who, f"Removed batch {bid}")


def allocate(c, uid, day, calc):
    """Take a sale's quantities from batches, soonest expiry first (not expired on the bill's date).
    c: an open transaction. Returns {line: [(batch_id, batch_no, qm)]} for the lines that have batches."""
    out = {}
    for i, l in enumerate(calc["lines"]):
        rows = c.execute("""SELECT b.id, b.batch_no, b.exp, b.qm - COALESCE((SELECT SUM(u.qm) FROM batch_use u WHERE u.batch_id=b.id), 0) AS left_qm
            FROM batch b WHERE b.active=1 AND b.item=? ORDER BY COALESCE(b.exp, '9999-12-31'), b.mfd, b.id""", (l["item"],)).fetchall()
        need = l["qm"]
        for r in rows:
            if need <= 0:
                break
            if r["left_qm"] <= 0 or (r["exp"] and r["exp"] < day):
                continue
            take = min(need, r["left_qm"])
            c.execute("INSERT INTO batch_use(bill_uid, line, batch_id, qm) VALUES(?,?,?,?)", (uid, i, r["id"], take))
            out.setdefault(i, []).append((r["id"], r["batch_no"] or "", take))
            need -= take
    return out


def give_back(c, ret_uid, orig_uid, lines):
    """A return: put the quantities back into the batches the original bill took them from (latest first)."""
    for l in lines:
        need = l["qm"]
        used = c.execute("""SELECT batch_id, SUM(qm) q FROM batch_use WHERE line=? AND (bill_uid=? OR bill_uid IN
            (SELECT uid FROM bill WHERE orig_uid=? AND status='ok')) GROUP BY batch_id ORDER BY MAX(id) DESC""",
                         (l["index"], orig_uid, orig_uid)).fetchall()
        for u in used:
            if need <= 0 or u["q"] <= 0:
                continue
            back = min(need, u["q"])
            c.execute("INSERT INTO batch_use(bill_uid, line, batch_id, qm) VALUES(?,?,?,?)", (ret_uid, l["index"], u["batch_id"], -back))
            need -= back


def release(c, uid):
    """A cancelled bill or return: its batch quantities no longer count."""
    c.execute("DELETE FROM batch_use WHERE bill_uid=?", (uid,))


def bill_batches(uid):
    """{line: [(batch_no, qm)]} for a bill (for Tally, when batches are sent)."""
    out = {}
    for r in db.rows("SELECT u.line, u.qm, b.batch_no FROM batch_use u JOIN batch b ON b.id=u.batch_id WHERE u.bill_uid=? ORDER BY u.id", (uid,)):
        out.setdefault(r["line"], []).append((r["batch_no"] or "", abs(r["qm"])))
    return out


def expiring(days=30):
    """Batches with stock that are expired or expire within `days`."""
    return [b for b in batches() if b["exp"] and b["days"] is not None and b["days"] <= days]
