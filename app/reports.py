"""Counter reports: the day's takings, GST by rate, HSN summary and document series (GSTR-1 tables 12 and 13).
Dukaan's own figures; the GST return itself is still filed from Tally."""
import csv
import io
import json
import re

import db
import gst as G


def _bills(frm, to, kinds=("sale", "return"), status=("ok",)):
    ks = ",".join("?" * len(kinds))
    ss = ",".join("?" * len(status))
    return db.rows(f"SELECT * FROM bill WHERE date BETWEEN ? AND ? AND kind IN ({ks}) AND status IN ({ss}) ORDER BY date, created",
                   (frm, to, *kinds, *status))


def summary(frm, to):
    sales = rets = n_s = n_r = 0
    modes = {"cash": 0, "upi": 0, "card": 0, "credit": 0}
    refunds = {"cash": 0, "upi": 0, "card": 0, "credit": 0}
    cashiers, counters, rates, items, days = {}, {}, {}, {}, {}
    savings = disc = 0
    offer_use, points = {}, {"used": 0, "value": 0, "earned": 0}
    for b in _bills(frm, to):
        calc, data = json.loads(b["calc"] or "null"), json.loads(b["data"])
        if not calc:
            continue
        sign = 1 if b["kind"] == "sale" else -1
        if b["kind"] == "sale":
            sales += calc["amount"]
            n_s += 1
            paid = 0
            for p in data["payments"]:
                modes[p["mode"]] += p["amount"]
                paid += p["amount"]
            modes["credit"] += calc["amount"] - paid
            savings += calc.get("savings", 0)
            disc += sum(l["disc"] for l in calc["lines"])
            for o in data.get("offers", []):
                u = offer_use.setdefault(o["name"], {"name": o["name"], "bills": 0, "amount": 0})
                u["bills"] += 1
                u["amount"] += o["amount"]
            if data.get("loyalty"):
                points["used"] += data["loyalty"]["points"]
                points["value"] += data["loyalty"]["value"]
            points["earned"] += data.get("points_earned", 0)
        else:
            rets += calc["amount"]
            n_r += 1
            refunds[data["refund"]] += calc["amount"]
        k = b["cashier"] or "-"
        cashiers[k] = cashiers.get(k, 0) + sign * calc["amount"]
        k = data.get("counter") or "-"
        counters[k] = counters.get(k, 0) + sign * calc["amount"]
        d = days.setdefault(b["date"], {"date": b["date"], "bills": 0, "amount": 0})
        d["amount"] += sign * calc["amount"]
        d["bills"] += 1 if sign > 0 else 0
        for t in calc["taxes"]:
            r = rates.setdefault(f"{t['gst']:g}", {"gst": t["gst"], "taxable": 0, "cgst": 0, "sgst": 0, "amount": 0})
            for f in ("taxable", "cgst", "sgst", "amount"):
                r[f] += sign * t[f]
        for l in calc["lines"]:
            it = items.setdefault(l["item"], {"item": l["item"], "qm": 0, "amount": 0})
            it["qm"] += sign * l["qm"]
            it["amount"] += sign * l["amount"]
        if calc.get("round_off"):
            r = rates.setdefault("round", {"gst": None, "taxable": 0, "cgst": 0, "sgst": 0, "amount": 0})
            r["amount"] += sign * calc["round_off"]
    pending = db.one("""SELECT COUNT(*) n FROM bill WHERE date BETWEEN ? AND ? AND status='ok' AND tally<>'posted'""", (frm, to))["n"]
    flagged = db.one("SELECT COUNT(*) n FROM bill WHERE date BETWEEN ? AND ? AND status='ok' AND flag<>''", (frm, to))["n"]
    cancelled = db.one("SELECT COUNT(*) n FROM bill WHERE date BETWEEN ? AND ? AND status='cancelled'", (frm, to))["n"]
    return {"from": frm, "to": to, "sales": sales, "bills": n_s, "returns": rets, "return_count": n_r, "net": sales - rets,
            "average": sales // n_s if n_s else 0, "modes": modes, "refunds": refunds,
            "cash_in_hand": modes["cash"] - refunds["cash"],
            "cashiers": sorted(cashiers.items(), key=lambda x: -x[1]), "counters": sorted(counters.items(), key=lambda x: -x[1]),
            "rates": sorted(rates.values(), key=lambda r: (r["gst"] is None, r["gst"] or 0)),
            "top": sorted(items.values(), key=lambda x: -x["amount"])[:25], "days": sorted(days.values(), key=lambda d: d["date"]),
            "savings": savings, "discount": disc, "offers": sorted(offer_use.values(), key=lambda x: -x["amount"]), "points": points, "not_in_tally": pending, "flagged": flagged, "cancelled": cancelled}


def hsn(frm, to):
    """HSN-wise summary, B2B and B2C apart (as GSTR-1 table 12 asks), net of returns."""
    out = {}
    for b in _bills(frm, to):
        calc, data = json.loads(b["calc"] or "null"), json.loads(b["data"])
        if not calc:
            continue
        sign = 1 if b["kind"] == "sale" else -1
        b2b = bool(data["buyer"].get("gstin"))
        dl = data["lines"]
        for i, l in enumerate(calc["lines"]):
            src = dl[i]
            code = re.sub(r"\s", "", src.get("hsn", ""))
            unit = src.get("unit", "")
            k = (b2b, code, f"{l['gst']:g}", unit)
            r = out.setdefault(k, {"b2b": b2b, "hsn": code, "gst": l["gst"], "unit": unit, "qm": 0, "taxable": 0,
                                   "tax": 0, "value": 0})
            r["qm"] += sign * l["qm"]
            r["taxable"] += sign * l["taxable"]
            r["tax"] += sign * (l["amount"] - l["taxable"])     # CGST + SGST on the line
            r["value"] += sign * l["amount"]
    for r in out.values():
        t = r.pop("tax")
        r["cgst"] = G.round_div(t, 2) if t >= 0 else -G.round_div(-t, 2)
        r["sgst"] = t - r["cgst"]
    return sorted(out.values(), key=lambda r: (r["b2b"], r["hsn"], r["gst"]))


def docs(frm, to):
    """Document series issued (GSTR-1 table 13): first and last number, how many, how many cancelled."""
    out = {}
    for b in _bills(frm, to, status=("ok", "cancelled")):
        m = re.match(r"^(.*/)(\d+)$", b["number"].split("#")[0])
        if not m:
            continue
        k = (b["kind"], m.group(1))
        n = int(m.group(2))
        r = out.setdefault(k, {"kind": "Invoices for outward supply" if b["kind"] == "sale" else "Credit notes",
                               "series": m.group(1), "first": n, "last": n, "count": 0, "cancelled": 0, "numbers": set()})
        r["first"], r["last"] = min(r["first"], n), max(r["last"], n)
        r["count"] += 1
        r["numbers"].add(n)
        if b["status"] == "cancelled":
            r["cancelled"] += 1
    res = []
    for r in out.values():
        missing = [n for n in range(r["first"], r["last"] + 1) if n not in r["numbers"]]
        res.append({"kind": r["kind"], "from": r["series"] + str(r["first"]), "to": r["series"] + str(r["last"]),
                    "total": r["last"] - r["first"] + 1, "cancelled": r["cancelled"] + len(missing),
                    "net": r["count"] - r["cancelled"], "missing": missing[:50]})
    return sorted(res, key=lambda r: (r["kind"], r["from"]))


def bills_csv(frm, to):
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(["Date", "Time", "Number", "Type", "Status", "Counter", "Cashier", "Customer", "GSTIN", "Taxable", "CGST",
                "SGST", "Round off", "Amount", "Cash", "UPI", "Card", "Credit", "In Tally"])
    for b in _bills(frm, to, status=("ok", "cancelled")):
        calc, data = json.loads(b["calc"] or "null") or {}, json.loads(b["data"])
        pays = {p["mode"]: p["amount"] for p in data.get("payments", [])}
        credit = (calc.get("amount", 0) - sum(pays.values())) if b["kind"] == "sale" else 0
        if b["kind"] == "return":
            pays = {data["refund"]: calc.get("amount", 0)}
        sign = 1 if b["kind"] == "sale" else -1
        r = lambda p: f"{sign * p / 100:.2f}"
        w.writerow([b["date"], b["time"], b["number"], "Bill" if b["kind"] == "sale" else "Return", b["status"],
                    data.get("counter", ""), b["cashier"], data["buyer"].get("ledger") or data["buyer"].get("name", ""),
                    data["buyer"].get("gstin", ""), r(calc.get("taxable", 0)), r(calc.get("cgst", 0)), r(calc.get("sgst", 0)),
                    r(calc.get("round_off", 0)), r(calc.get("amount", 0)), r(pays.get("cash", 0)), r(pays.get("upi", 0)),
                    r(pays.get("card", 0)), r(credit if b["kind"] == "sale" else pays.get("credit", 0)),
                    {"posted": "Yes", "skip": "-"}.get(b["tally"], "No")])
    return f.getvalue()


def hsn_csv(frm, to):
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(["B2B/B2C", "HSN", "UQC/Unit", "Total quantity", "Total value", "Rate", "Taxable value", "Integrated tax",
                "Central tax", "State/UT tax", "Cess"])
    for r in hsn(frm, to):
        w.writerow(["B2B" if r["b2b"] else "B2C", r["hsn"], r["unit"], G.qty(r["qm"]), f"{r['value'] / 100:.2f}",
                    f"{r['gst']:g}", f"{r['taxable'] / 100:.2f}", "0.00", f"{r['cgst'] / 100:.2f}", f"{r['sgst'] / 100:.2f}", "0.00"])
    return f.getvalue()
