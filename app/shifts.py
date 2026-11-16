"""Day-end cash: a counter's day (shift) from opening cash to closing count.

Expected cash in the drawer = opening cash + cash taken on bills - cash refunded on returns
                              + cash put in - cash taken out.
At closing the cashier counts the notes and coins; the difference (short or extra) is kept with the closing.
"""
import json
import time

import db
import gst as G

DENOMS = [50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100]   # paise: Rs 500 note ... Rs 1 coin


def current(counter_id):
    return db.one("SELECT * FROM shift WHERE counter_id=? AND closed_at IS NULL ORDER BY id DESC LIMIT 1", (counter_id,))


def summary(sh, until=None):
    until = until or time.time()
    s = {"bills": 0, "sales": 0, "returns": 0, "return_count": 0, "cash": 0, "upi": 0, "card": 0, "credit": 0,
         "refund_cash": 0, "refund_other": 0, "discount": 0, "cancelled": 0, "cash_in": 0, "cash_out": 0, "moves": []}
    for b in db.rows("""SELECT kind, status, data, calc FROM bill WHERE counter_id=? AND created >= ? AND created <= ?""",
                     (sh["counter_id"], sh["opened_at"], until)):
        if b["status"] != "ok":
            s["cancelled"] += 1
            continue
        data, calc = json.loads(b["data"]), json.loads(b["calc"] or "null")
        if not calc:
            continue
        if b["kind"] == "sale":
            s["bills"] += 1
            s["sales"] += calc["amount"]
            paid = 0
            for p in data.get("payments", []):
                s[p["mode"]] += p["amount"]
                paid += p["amount"]
            s["credit"] += calc["amount"] - paid
            s["discount"] += sum(l["disc"] for l in calc["lines"])
        else:
            s["return_count"] += 1
            s["returns"] += calc["amount"]
            if data.get("refund") == "cash":
                s["refund_cash"] += calc["amount"]
            else:
                s["refund_other"] += calc["amount"]
    for m in db.rows("SELECT * FROM cash_move WHERE shift_id=? ORDER BY id", (sh["id"],)):
        s["cash_in" if m["kind"] == "in" else "cash_out"] += m["amount"]
        s["moves"].append(m)
    s["opening"] = sh["opening"]
    s["expected"] = sh["opening"] + s["cash"] - s["refund_cash"] + s["cash_in"] - s["cash_out"]
    return s


def open_shift(counter_id, opening, who):
    if not isinstance(opening, int) or opening < 0:
        raise ValueError("Enter the cash in the drawer.")
    if current(counter_id):
        raise ValueError("This counter's day is already started.")
    with db.tx() as c:
        c.execute("INSERT INTO shift(counter_id, opened_by, opened_at, opening) VALUES(?,?,?,?)", (counter_id, who, time.time(), opening))
    db.log(who, f"Started the day on counter {counter_id} with {G.rs(opening)}")
    return current(counter_id)


def move(counter_id, kind, amount, reason, who):
    sh = current(counter_id)
    if not sh:
        raise ValueError("Start the day first.")
    if kind not in ("in", "out") or not isinstance(amount, int) or amount <= 0:
        raise ValueError("Enter the amount.")
    reason = str(reason or "").strip()[:120]
    if not reason:
        raise ValueError("Say what the cash is for.")
    with db.tx() as c:
        c.execute("INSERT INTO cash_move(shift_id, counter_id, at, kind, amount, reason, by) VALUES(?,?,?,?,?,?,?)",
                  (sh["id"], counter_id, time.time(), kind, amount, reason, who))
    db.log(who, f"Cash {'in' if kind == 'in' else 'out'} {G.rs(amount)}: {reason}")


def close(counter_id, denoms, kept, note, who):
    sh = current(counter_id)
    if not sh:
        raise ValueError("This counter's day isn't started.")
    counted = 0
    clean = {}
    for k, n in (denoms or {}).items():
        k, n = int(k), int(n or 0)
        if k not in DENOMS or n < 0:
            raise ValueError("The note count isn't right.")
        if n:
            clean[str(k)] = n
            counted += k * n
    kept = int(kept or 0)
    if kept < 0 or kept > counted:
        raise ValueError("Cash kept for tomorrow can't be more than what's counted.")
    now = time.time()
    s = summary(sh, now)
    diff = counted - s["expected"]
    s.pop("moves")
    with db.tx() as c:
        c.execute("""UPDATE shift SET closed_by=?, closed_at=?, counted=?, denoms=?, expected=?, diff=?, kept=?, note=?, summary=?
            WHERE id=?""", (who, now, counted, json.dumps(clean), s["expected"], diff, kept, str(note or "")[:300], json.dumps(s), sh["id"]))
    db.log(who, f"Closed the day on counter {counter_id}: expected {G.rs(s['expected'])}, counted {G.rs(counted)}, "
                f"{'short' if diff < 0 else 'extra'} {G.rs(abs(diff))}")
    return db.one("SELECT * FROM shift WHERE id=?", (sh["id"],))


def history(frm, to):
    rows = db.rows("""SELECT s.*, c.name counter FROM shift s LEFT JOIN counter c ON c.id=s.counter_id
        WHERE date(s.opened_at, 'unixepoch', 'localtime') BETWEEN ? AND ? ORDER BY s.opened_at DESC""", (frm, to))
    for r in rows:
        r["summary"] = json.loads(r["summary"] or "null")
        r["denoms"] = json.loads(r["denoms"] or "{}")
    return rows
