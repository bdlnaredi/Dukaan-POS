"""Offers the owner sets up; the counter applies them by itself (static/offers.js has the rules).

  bxgy  buy X get Y free of the same item (e.g. buy 2 get 1): the free units are a discount on the line
  off   % or Rs off per unit of some items or item groups, optionally from a minimum quantity
  bill  % or Rs off the whole bill from a minimum bill amount (with an optional cap)

A free or cheaper item is a discount shown on the invoice, so GST is worked out on what the customer pays.
"""
import json
import time
from datetime import date

import db

KINDS = ("bxgy", "off", "bill")


def _int(x, label, lo=0, hi=10 ** 9):
    try:
        v = int(x or 0)
    except (TypeError, ValueError):
        raise ValueError(f"{label} isn't a whole number.")
    if not lo <= v <= hi:
        raise ValueError(f"{label} is out of range.")
    return v


def clean(d):
    kind = d.get("kind")
    if kind not in KINDS:
        raise ValueError("Pick the kind of offer.")
    name = str(d.get("name") or "").strip()[:60]
    if not name:
        raise ValueError("Name the offer, e.g. Buy 2 get 1 on Parle-G.")
    x = {}
    if kind in ("bxgy", "off"):
        x["items"] = [str(i) for i in (d.get("items") or []) if str(i).strip()][:200]
        x["groups"] = [str(g) for g in (d.get("groups") or []) if str(g).strip()][:50]
        if not x["items"] and not x["groups"]:
            raise ValueError("Pick the items or item groups the offer is on.")
    if kind == "bxgy":
        x["buy"] = _int(d.get("buy"), "Buy", 1, 1000)
        x["free"] = _int(d.get("free"), "Free", 1, 1000)
    elif kind == "off":
        x["pct"] = float(d.get("pct") or 0)
        x["amt"] = _int(d.get("amt"), "Amount off", 0, 10 ** 8)
        x["min_qty"] = _int(d.get("min_qty"), "Minimum quantity", 0, 100000)
        if not (0 < x["pct"] <= 90) and not x["amt"]:
            raise ValueError("Give a % off (up to 90) or an amount off per unit.")
    else:
        x["min_bill"] = _int(d.get("min_bill"), "Minimum bill", 0, 10 ** 10)
        x["pct"] = float(d.get("pct") or 0)
        x["amt"] = _int(d.get("amt"), "Amount off", 0, 10 ** 9)
        x["max"] = _int(d.get("max"), "Most off", 0, 10 ** 9)
        if not (0 < x["pct"] <= 90) and not x["amt"]:
            raise ValueError("Give a % off (up to 90) or an amount off the bill.")
        if x["amt"] and x["min_bill"] and x["amt"] >= x["min_bill"]:
            raise ValueError("The amount off must be less than the minimum bill.")
    start, end = str(d.get("start") or ""), str(d.get("end") or "")
    for v, label in ((start, "Start"), (end, "End")):
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise ValueError(f"{label} date isn't right.")
    if start and end and end < start:
        raise ValueError("The offer ends before it starts.")
    return {"name": name, "kind": kind, "data": x, "start": start, "end": end, "active": bool(d.get("active", True))}


def save(d, who):
    o = clean(d)
    with db.tx() as c:
        if d.get("id"):
            c.execute("UPDATE offer SET name=?, kind=?, data=?, start=?, end=?, active=? WHERE id=?",
                      (o["name"], o["kind"], json.dumps(o["data"]), o["start"], o["end"], 1 if o["active"] else 0, d["id"]))
        else:
            c.execute("INSERT INTO offer(name, kind, data, start, end, active, created) VALUES(?,?,?,?,?,?,?)",
                      (o["name"], o["kind"], json.dumps(o["data"]), o["start"], o["end"], 1 if o["active"] else 0, time.time()))
    db.log(who, f"Offer saved: {o['name']}")


def all_offers():
    return [{**r, "data": json.loads(r["data"]), "active": bool(r["active"])} for r in db.rows("SELECT * FROM offer ORDER BY id DESC")]


def for_counters():
    """Offers that are on, today or later (the counter checks the dates on the bill's day)."""
    today = date.today().isoformat()
    return [o for o in all_offers() if o["active"] and (not o["end"] or o["end"] >= today)]


def remove(oid, who):
    with db.tx() as c:
        c.execute("DELETE FROM offer WHERE id=?", (oid,))
    db.log(who, f"Offer {oid} removed")
