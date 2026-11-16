"""Bills and returns: checking what a counter sends, numbering, and the vouchers each one becomes in Tally.

A bill is already in the customer's hand when it reaches the server (the counter may have been offline),
so the server never changes a bill. It works every figure out again; if anything disagrees (a total, a GST
rate that changed in Tally, a number used twice) the bill is kept but marked for the owner to look at, and it
waits before going to Tally.
"""
import json
import re
import secrets
import time
from datetime import date, datetime

import db
import gst as G
import loyalty
import stock
import vouchers as V

UID = re.compile(r"^[A-Za-z0-9-]{8,40}$")
MODES = ("cash", "upi", "card")
MODE_NAMES = {"cash": "Cash", "upi": "UPI", "card": "Card", "credit": "Credit"}
MAX_NUMBER = 16          # GST: an invoice number has at most 16 characters


def fy_of(day):
    d = date.fromisoformat(day)
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def number(series, fy, n, kind="sale"):
    prefix = (db.get("settings", {}) or {}).get("prefix", "")
    return f"{prefix}{'CN' if kind == 'return' else ''}{series}/{fy}/{n}"


def _num_re(series, fy, kind):
    prefix = (db.get("settings", {}) or {}).get("prefix", "")
    return re.compile("^" + re.escape(f"{prefix}{'CN' if kind == 'return' else ''}{series}/{fy}/") + r"(\d{1,7})$")


# ---------------------------------------------------------------- items as the counter sees them

BARCODE = re.compile(r"^[0-9A-Za-z-]{4,32}$")


def items(include_hidden=False):
    rows = db.rows("""SELECT i.name, i.parent, i.unit, i.hsn t_hsn, i.gst t_gst, i.gst_from, i.price t_price, i.mrp t_mrp,
        i.aliases, i.stock, l.price, l.mrp, l.barcodes, l.loose, l.fav, l.hsn, l.gst, l.hidden, l.min_stock, u.decimals
        FROM item i LEFT JOIN item_local l ON l.name=i.name LEFT JOIN unit u ON u.name=i.unit ORDER BY i.name""")
    out = []
    hsn_digits = (db.get("settings", {}) or {}).get("hsn_digits", 6)
    live = stock.live()
    bat = stock.batch_summary()
    for r in rows:
        if r["hidden"] and not include_hidden:
            continue
        aliases = json.loads(r["aliases"] or "[]")
        codes = json.loads(r["barcodes"] or "[]") + [a for a in aliases if re.fullmatch(r"\d{6,14}", a)]
        hsn = r["hsn"] or r["t_hsn"] or ""
        g = r["gst"] if r["gst"] is not None else r["t_gst"]
        price = r["price"] if r["price"] is not None else (r["t_price"] or 0)
        mrp = r["mrp"] if r["mrp"] is not None else (r["t_mrp"] or 0)
        issues = []
        if g is None:
            issues.append("No GST rate in Tally")
        elif g not in G.SLABS:
            issues.append(f"GST rate {g:g}% isn't a GST rate")
            g = None
        if not hsn:
            issues.append("No HSN code")
        elif len(re.sub(r"\D", "", hsn)) < hsn_digits:
            issues.append(f"HSN code is shorter than {hsn_digits} digits")
        if not price:
            issues.append("No selling price")
        if mrp and price > mrp:
            issues.append("Price is above MRP")
        out.append({
            "name": r["name"], "group": r["parent"], "unit": r["unit"] or "",
            "decimals": r["decimals"] if r["decimals"] is not None else (3 if r["loose"] else 0),
            "hsn": hsn, "gst": g, "gst_from": "dukaan" if r["gst"] is not None else (r["gst_from"] or ""),
            "price": price, "mrp": mrp, "codes": sorted(set(codes)), "aliases": [a for a in aliases if a not in codes],
            "stock": live.get(r["name"], r["stock"]), "tally_stock": r["stock"], "min_stock": r["min_stock"],
            "low": r["min_stock"] is not None and live.get(r["name"], r["stock"] or 0) <= r["min_stock"],
            "batches": bat.get(r["name"], []),
            "loose": bool(r["loose"]), "fav": bool(r["fav"]), "hidden": bool(r["hidden"]),
            "tally": {"hsn": r["t_hsn"] or "", "gst": r["t_gst"], "price": r["t_price"] or 0, "mrp": r["t_mrp"] or 0},
            "issues": issues, "can_sell": g is not None and bool(hsn) and not (mrp and price > mrp),
        })
    return out


def item_map():
    return {i["name"]: i for i in items(include_hidden=True)}


def customers():
    """Tally parties a counter can bill on credit or to a business (GSTIN)."""
    return [{"name": r["name"], "gstin": r["gstin"] or "", "mobile": r["mobile"] or "",
             "due": round(r["closing"] or 0, 2)}
            for r in db.rows("SELECT name, gstin, mobile, closing FROM ledger WHERE chain LIKE '%|Sundry Debtors|%' ORDER BY name")]


def is_debtor(name):
    return bool(db.one("SELECT 1 x FROM ledger WHERE name=? AND chain LIKE '%|Sundry Debtors|%'", (name,)))


# ---------------------------------------------------------------- a sale from a counter

def _s(x, n=200):
    return re.sub(r"\s+", " ", str(x or "")).strip()[:n]


def _rate(g):
    try:
        return float(g)
    except (TypeError, ValueError):
        return None


def accept(p, sess):
    """Store one bill sent by a counter. Returns {uid, status, number, flag}. Sending the same bill again is fine."""
    uid = str(p.get("uid") or "")
    if not UID.match(uid):
        raise ValueError("Bill id isn't right.")
    old = db.one("SELECT uid, number, status, flag, tally FROM bill WHERE uid=?", (uid,))
    if old:
        return {"uid": uid, "number": old["number"], "status": old["status"], "flag": old["flag"], "again": True}
    try:
        day = date.fromisoformat(str(p.get("date")))
    except ValueError:
        raise ValueError("Bill date isn't right.")
    flags = []
    if day > date.today():
        flags.append("Bill date is in the future")
    tm = _s(p.get("time"), 8)
    counter = db.one("SELECT * FROM counter WHERE id=?", (p.get("counter_id"),))
    if not counter:
        raise ValueError("This counter isn't set up on the server. Ask the owner to add it in Settings.")
    if counter["device"] and sess.get("device") and counter["device"] != sess["device"]:
        flags.append("Bill came from a device that no longer holds this counter")
    fy = fy_of(day.isoformat())
    num = _s(p.get("number"), 40)
    m = _num_re(counter["series"], fy, "sale").match(num)
    if not m:
        raise ValueError(f"Bill number {num} isn't in this counter's series.")
    if len(num) > MAX_NUMBER:
        raise ValueError(f"Bill number {num} is longer than 16 characters.")
    n = int(m.group(1))

    buyer = p.get("buyer") or {}
    buyer = {k: _s(buyer.get(k), 300 if k == "address" else 120) for k in ("name", "phone", "address", "gstin", "ledger")}
    buyer["gstin"] = buyer["gstin"].upper().replace(" ", "")
    if buyer["ledger"] and not db.one("SELECT 1 x FROM ledger WHERE name=?", (buyer["ledger"],)):
        flags.append(f"Customer {buyer['ledger']} isn't in Tally")
    imap = item_map()
    lines = []
    for x in (p.get("lines") or [])[:300]:
        name = _s(x.get("item"))
        it = imap.get(name)
        if not it:
            flags.append(f"{name} isn't a stock item in Tally")
        elif it["gst"] is None:
            flags.append(f"{name} has no GST rate in Tally")
        elif _rate(x.get("gst")) != float(it["gst"]):
            flags.append(f"{name} was billed at {x.get('gst')}% GST but Tally now says {it['gst']:g}%")
        hsn = _s(x.get("hsn"), 12) or (it or {}).get("hsn", "")
        if not hsn:
            flags.append(f"{name} has no HSN code")
        lines.append({"item": name, "qty": str(x.get("qty")), "price": x.get("price"), "mrp": x.get("mrp") or 0,
                      "gst": x.get("gst"), "disc": x.get("disc") or 0, "hsn": hsn, "unit": (it or {}).get("unit", "")})
    bill = {"lines": lines, "bill_disc": p.get("bill_disc") or 0, "round": bool(p.get("round", True)), "buyer": buyer}
    try:
        calc = G.compute(bill)
        err = ""
    except G.GSTError as e:
        calc, err = None, str(e)
        flags.append("The bill's figures don't work out: " + err)
    stated = p.get("amount")
    if calc and stated is not None and stated != calc["amount"]:
        flags.append(f"Counter's total {G.rs(stated)} isn't what the server works out ({G.rs(calc['amount'])})")

    pays = []
    for x in p.get("payments") or []:
        mode, amt = str(x.get("mode")), x.get("amount")
        if mode not in MODES or not isinstance(amt, int) or amt < 0:
            raise ValueError("A payment on the bill isn't right.")
        if amt:
            pays.append({"mode": mode, "amount": amt, "ref": _s(x.get("ref"), 40)})
    paid = sum(x["amount"] for x in pays)
    if calc:
        if paid > calc["amount"]:
            flags.append("Payments add up to more than the bill")
        elif paid < calc["amount"]:
            if not buyer["ledger"]:
                flags.append("Bill isn't fully paid and has no customer to put it on credit")
            elif not (sess.get("role") == "owner" or sess.get("can_credit")):
                flags.append(f"{sess.get('name')} isn't allowed to give credit")
    if buyer["gstin"] and not G.gstin_ok(buyer["gstin"]):
        flags.append("Customer's GSTIN isn't right")
    data = {"buyer": buyer, "lines": lines, "bill_disc": bill["bill_disc"], "round": bill["round"], "payments": pays,
            "tendered": p.get("tendered") if isinstance(p.get("tendered"), int) else None,
            "counter": counter["name"], "offline": bool(p.get("offline"))}
    offers_used = []
    for o in (p.get("offers") or [])[:30]:
        if isinstance(o, dict) and isinstance(o.get("amount"), int) and o["amount"] > 0:
            offers_used.append({"id": o.get("id"), "name": _s(o.get("name"), 60), "amount": o["amount"]})
    if offers_used:
        data["offers"] = offers_used
    redeem = p.get("loyalty") if isinstance(p.get("loyalty"), dict) else None
    if redeem and isinstance(redeem.get("points"), int) and redeem["points"] > 0 and isinstance(redeem.get("value"), int):
        data["loyalty"] = {"points": redeem["points"], "value": redeem["value"]}
        if redeem["value"] > bill["bill_disc"]:
            flags.append("Points used are worth more than the bill discount")
    else:
        redeem = None
    flag = "; ".join(dict.fromkeys(flags))
    with db.tx() as c:
        clash = c.execute("SELECT uid FROM bill WHERE kind='sale' AND number=?", (num,)).fetchone()
        if clash:
            flag = ("; " if flag else "") + flag
            flag = f"Bill number {num} was used twice (by bill {clash['uid'][:8]})" + flag
            num_store = f"{num}#{uid[:6]}"
        else:
            num_store = num
        link = str(p.get("link") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,20}", link) or c.execute("SELECT 1 FROM bill WHERE link=?", (link,)).fetchone():
            link = secrets.token_urlsafe(9)
        c.execute("""INSERT INTO bill(uid, kind, number, fy, counter_id, cashier_id, cashier, date, time, created, received,
            data, calc, amount, status, flag, link, tally) VALUES(?, 'sale', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?, 'pending')""",
                  (uid, num_store, fy, counter["id"], sess.get("cashier_id"), sess.get("name"), day.isoformat(), tm,
                   p.get("created") or time.time(), time.time(), json.dumps(data), json.dumps(calc) if calc else None,
                   calc["amount"] if calc else (stated or 0), flag, link))
        c.execute("""INSERT INTO numbering(counter_id, fy, kind, last) VALUES(?, ?, 'sale', ?)
            ON CONFLICT(counter_id, fy, kind) DO UPDATE SET last=MAX(last, excluded.last)""", (counter["id"], fy, n))
        if calc:
            stock.allocate(c, uid, day.isoformat(), calc)
            earned, lflag = loyalty.on_sale(c, uid, num_store, buyer, calc, data.get("loyalty"))
            if earned or lflag:
                data["points_earned"] = earned
                if lflag:
                    flag = (flag + "; " if flag else "") + lflag
                c.execute("UPDATE bill SET data=?, flag=? WHERE uid=?", (json.dumps(data), flag, uid))
    return {"uid": uid, "number": num_store, "status": "ok", "flag": flag, "link": link}


def last_number(counter_id, fy, kind="sale"):
    r = db.one("SELECT last FROM numbering WHERE counter_id=? AND fy=? AND kind=?", (counter_id, fy, kind))
    return r["last"] if r else 0


# ---------------------------------------------------------------- returns (credit notes)

def returned_so_far(orig_uid):
    """{line index: (thousandths, paise) already returned} for a bill."""
    out = {}
    for r in db.rows("SELECT data FROM bill WHERE kind='return' AND orig_uid=? AND status='ok'", (orig_uid,)):
        for x in json.loads(r["data"])["lines"]:
            q, a = out.get(x["index"], (0, 0))
            out[x["index"]] = (q + x["qm"], a + x["amount"])
    return out


def return_preview(orig_uid, picks):
    b = db.one("SELECT * FROM bill WHERE uid=? AND kind='sale'", (orig_uid,))
    if not b or b["status"] != "ok" or not b["calc"]:
        raise ValueError("That bill can't be returned against.")
    calc = json.loads(b["calc"])
    data = json.loads(b["data"])
    done = returned_so_far(orig_uid)
    lines = []
    for pk in picks:
        i = int(pk.get("index", -1))
        if not 0 <= i < len(calc["lines"]):
            raise ValueError("Pick items from the bill.")
        ol = calc["lines"][i]
        qm = G.milli(pk.get("qty"))
        if qm <= 0:
            continue
        dq, da = done.get(i, (0, 0))
        left = ol["qm"] - dq
        if qm > left:
            raise ValueError(f"Only {G.qty(left)} of {ol['item']} can still be returned.")
        # this share of what the customer paid for the line (GST included); the last of it takes what is left
        amt = ol["amount"] - da if qm == left else min(G.round_div(ol["amount"] * qm, ol["qm"]), ol["amount"] - da)
        lines.append({"index": i, "item": ol["item"], "qm": qm, "gst": ol["gst"], "amount": amt, "price": ol["price"],
                      "hsn": data["lines"][i].get("hsn", ""), "unit": data["lines"][i].get("unit", "")})
    calc_r = G.compute_return(lines, data.get("round", True))
    return b, data, lines, calc_r


def make_return(p, sess):
    if not (sess.get("role") == "owner" or sess.get("can_return")):
        raise PermissionError("Only the owner, or a cashier allowed to, can take returns.")
    uid = str(p.get("uid") or "")
    if not UID.match(uid):
        raise ValueError("Return id isn't right.")
    if db.one("SELECT 1 x FROM bill WHERE uid=?", (uid,)):
        r = db.one("SELECT uid, number, status FROM bill WHERE uid=?", (uid,))
        return {**r, "again": True}
    orig, odata, lines, calc = return_preview(str(p.get("orig_uid")), p.get("lines") or [])
    refund = str(p.get("refund") or "cash")
    if refund not in MODES + ("credit",):
        raise ValueError("Pick how the money goes back.")
    if refund == "credit" and not odata["buyer"].get("ledger"):
        raise ValueError("The bill wasn't on a customer's account, so the return can't go to one. Refund it instead.")
    counter = db.one("SELECT * FROM counter WHERE id=?", (orig["counter_id"],))
    counter = db.one("SELECT * FROM counter WHERE id=?", (p.get("counter_id"),)) or counter
    today = date.today().isoformat()
    fy = fy_of(today)
    with db.tx() as c:
        r = c.execute("SELECT last FROM numbering WHERE counter_id=? AND fy=? AND kind='return'", (counter["id"], fy)).fetchone()
        n = (r["last"] if r else 0) + 1
        num = number(counter["series"], fy, n, "return")
        if len(num) > MAX_NUMBER:
            raise ValueError(f"Return number {num} is longer than 16 characters. Shorten the bill number prefix.")
        c.execute("""INSERT INTO numbering(counter_id, fy, kind, last) VALUES(?, ?, 'return', ?)
            ON CONFLICT(counter_id, fy, kind) DO UPDATE SET last=excluded.last""", (counter["id"], fy, n))
        data = {"buyer": odata["buyer"], "lines": [{k: l[k] for k in ("index", "item", "qm", "gst", "amount", "hsn", "unit")}
                                                  for l in lines],
                "refund": refund, "reason": _s(p.get("reason"), 200), "orig_number": orig["number"],
                "orig_date": orig["date"], "counter": counter["name"], "round": odata.get("round", True)}
        c.execute("""INSERT INTO bill(uid, kind, number, fy, counter_id, cashier_id, cashier, date, time, created, received,
            data, calc, amount, status, flag, link, orig_uid, tally) VALUES(?, 'return', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', '', ?, ?, 'pending')""",
                  (uid, num, fy, counter["id"], sess.get("cashier_id"), sess.get("name"), today,
                   datetime.now().strftime("%H:%M:%S"), time.time(), time.time(), json.dumps(data), json.dumps(calc),
                   calc["amount"], secrets.token_urlsafe(9), orig["uid"]))
        stock.give_back(c, uid, orig["uid"], lines)
        loyalty.on_return(c, uid, orig["uid"], calc["amount"], orig["amount"])
    db.log(sess.get("name"), f"Return {num} against {orig['number']}: {G.rs(calc['amount'])}")
    return {"uid": uid, "number": num, "status": "ok", "amount": calc["amount"]}


# ---------------------------------------------------------------- cancelling and approving

def gst_period_open(day):
    """A bill can still be cancelled in Tally until GSTR-1 for its month is due (the 11th of the next month).
    After that it is in the return, so the goods come back as a credit note instead."""
    d, t = date.fromisoformat(day), date.today()
    months = (t.year - d.year) * 12 + t.month - d.month
    return months == 0 or (months == 1 and t.day <= 10)


def cancel(uid, sess, reason):
    if sess.get("role") != "owner":
        raise PermissionError("Only the owner can cancel a bill.")
    reason = _s(reason, 200)
    if not reason:
        raise ValueError("Say why the bill is being cancelled.")
    b = db.one("SELECT * FROM bill WHERE uid=?", (uid,))
    if not b or b["status"] != "ok":
        raise ValueError("That bill can't be cancelled.")
    if db.one("SELECT 1 x FROM bill WHERE orig_uid=? AND status='ok'", (uid,)):
        raise ValueError("Items on this bill were returned. Cancel the return first, or take the rest back as a return.")
    in_tally = bool(db.one("SELECT 1 x FROM post WHERE bill_uid=? AND status IN ('done', 'sending', 'unknown')", (uid,)))
    if in_tally and not gst_period_open(b["date"]):
        raise ValueError("GSTR-1 for this bill's month is already due, so it can't be cancelled now. Take the goods back as a return (credit note) instead.")
    with db.tx() as c:
        c.execute("UPDATE bill SET status='cancelled', tally=?, tally_error='', cancelled_by=?, cancel_reason=?, cancelled_at=? WHERE uid=?",
                  ("cancelling" if in_tally else "skip", sess.get("name"), reason, time.time(), uid))
        stock.release(c, uid)
        loyalty.on_cancel(c, uid)
        c.execute("DELETE FROM post WHERE bill_uid=? AND status IN ('queued', 'failed')", (uid,))
    db.log(sess.get("name"), f"Cancelled {b['number']}{' (and in Tally)' if in_tally else ''}: {reason}")
    return {"in_tally": in_tally}


def approve(uid, sess):
    """The owner has looked at a marked bill and lets it go to Tally as it is."""
    if sess.get("role") != "owner":
        raise PermissionError("Only the owner can do this.")
    b = db.one("SELECT * FROM bill WHERE uid=?", (uid,))
    if not b or not b["flag"]:
        return
    if not b["calc"]:
        raise ValueError("This bill's figures don't work out, so it can't go to Tally. Cancel it and bill again.")
    with db.tx() as c:
        c.execute("UPDATE bill SET flag='', tally='pending', tally_error='' WHERE uid=?", (uid,))
    db.log(sess.get("name"), f"Approved bill {b['number']} (was: {b['flag']})")


# ---------------------------------------------------------------- what goes to Tally

def plan(b, setup, info):
    """[(kind, label, xml)] for a stored bill, in the order they go to Tally."""
    data, calc = json.loads(b["data"]), json.loads(b["calc"])
    company = setup["company"]
    led = {"cash": setup["cash"], "upi": setup["upi"], "card": setup["card"]}
    units = {l["item"]: l.get("unit", "") for l in data["lines"]}
    buyer = data["buyer"]
    tag = lambda seq: f" [dk:{b['uid']}-{seq}]"
    who = f" by {b['cashier']}" if b["cashier"] else ""
    cust = " ".join(x for x in (buyer.get("name"), buyer.get("phone")) if x)
    bats = None
    if setup.get("send_batches"):
        used = stock.bill_batches(b["uid"])
        if b["kind"] == "sale":
            bats = used
        else:            # a return's batch rows are kept under the original bill's line numbers
            bats = {j: used.get(l.get("index"), []) for j, l in enumerate(data["lines"])}
    out = []
    if b["kind"] == "sale":
        pays = data["payments"]
        paid = "; ".join(f"{MODE_NAMES[x['mode']]} {G.rs(x['amount'])}" for x in pays)
        if calc["amount"] > sum(x["amount"] for x in pays):
            paid = (paid + "; " if paid else "") + f"Credit {G.rs(calc['amount'] - sum(x['amount'] for x in pays))}"
        base = f"Dukaan bill {b['number']} at {data['counter']}{who}. {paid}." + (f" Customer: {cust}." if cust else "")
        if buyer.get("ledger"):
            party, debtor = buyer["ledger"], is_debtor(buyer["ledger"])
            out.append(("sales", f"Bill {b['number']} to {party}", V.invoice(
                setup, company, info, sale=True, day=b["date"], number=b["number"], party=party, debtor=debtor,
                calc=calc, units=units, buyer=buyer, narration=base + tag(1), batches=bats)))
            for i, x in enumerate(pays, 2):
                out.append(("receipt", f"Receipt {G.rs(x['amount'])} {MODE_NAMES[x['mode']]} for bill {b['number']}", V.receipt(
                    setup, company, day=b["date"], party=party, account=led[x["mode"]], amount=x["amount"], bill_no=b["number"],
                    narration=f"Paid at the counter for Dukaan bill {b['number']}{who}" + tag(i))))
        else:
            modes = [x["mode"] for x in pays]
            party = led["cash"] if ("cash" in modes or not modes) else led[modes[0]]
            out.append(("sales", f"Bill {b['number']}", V.invoice(
                setup, company, info, sale=True, day=b["date"], number=b["number"], party=party, debtor=False,
                calc=calc, units=units, buyer=buyer, narration=base + tag(1), batches=bats)))
            seq = 2
            for x in pays:
                if led[x["mode"]] != party:
                    out.append(("contra", f"{MODE_NAMES[x['mode']]} part of bill {b['number']}", V.contra(
                        setup, company, day=b["date"], frm=party, to=led[x["mode"]], amount=x["amount"],
                        narration=f"{MODE_NAMES[x['mode']]} part of Dukaan bill {b['number']}" + tag(seq))))
                    seq += 1
    else:
        refund = data["refund"]
        party = buyer["ledger"] if refund == "credit" else led[refund]
        debtor = refund == "credit" and is_debtor(party)
        nar = (f"Dukaan return {b['number']} against bill {data['orig_number']} ({data['orig_date']}){who}. "
               f"Refund: {MODE_NAMES[refund]}." + (f" Reason: {data['reason']}." if data.get("reason") else "")
               + (f" Customer: {cust}." if cust else ""))
        out.append(("creditnote", f"Return {b['number']}", V.invoice(
            setup, company, info, sale=False, day=b["date"], number=b["number"], party=party, debtor=debtor,
            calc=calc, units=units, buyer=buyer, narration=nar + tag(1), ref=(data["orig_number"], data["orig_date"]), batches=bats)))
    return out
