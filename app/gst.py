"""Dukaan GST maths. The same rules are in static/gst.js; tests/gst_cases.json keeps the two in step.

Money is whole paise and quantities are thousandths of a unit (integers), so the phone, the counter PC
and the server always get exactly the same figures.

Prices are what the customer pays per unit, GST included. For each GST rate on the bill: tax = the
rate's share of the amount, split equally into CGST and SGST; taxable value = the rest. Counter sales
are always within the state (the place of supply is the shop), so there is no IGST.
"""
import re

SLABS = [0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28, 40]
B2C_DETAILS_FROM = 5000000   # Rule 46: name, address and state of an unregistered buyer from Rs 50,000


class GSTError(ValueError):
    pass


def round_div(a, b):
    """round(a / b) to the nearest whole number, halves up; a >= 0, b > 0."""
    return (2 * a + b) // (2 * b)


_QTY = re.compile(r"^(\d+)?(?:\.(\d{0,3}))?$")


def milli(q):
    s = str("" if q is None else q).strip()
    m = _QTY.match(s)
    if not s or not m or (m.group(1) is None and not m.group(2)):
        raise GSTError(f"Quantity {s} isn't a number with up to 3 decimals.")
    return int(m.group(1) or 0) * 1000 + int((m.group(2) or "").ljust(3, "0"))


def rate_h(g):
    try:
        x = float(g)
    except (TypeError, ValueError):
        raise GSTError(f"{g}% isn't a GST rate.")
    if x not in SLABS:
        raise GSTError(f"{g}% isn't a GST rate.")
    return round(x * 100)


def _int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _num(x):
    """A GST rate as JSON writes it: 18 not 18.0, 0.25 stays."""
    f = float(x)
    return int(f) if f == int(f) else f


def compute(bill):
    src = bill.get("lines") or []
    if not src:
        raise GSTError("Add at least one item.")
    lines = []
    for l in src:
        name = str(l.get("item") or "")
        qm = milli(l.get("qty"))
        if qm <= 0:
            raise GSTError(f"Quantity of {name} must be more than zero.")
        if qm > 100000000:
            raise GSTError(f"Quantity of {name} is too large.")
        price, mrp, disc = l.get("price"), l.get("mrp") or 0, l.get("disc") or 0
        if not _int(price) or price <= 0:
            raise GSTError(f"Price of {name} must be more than zero.")
        if price > 10000000:
            raise GSTError(f"Price of {name} is too large (over Rs 1,00,000 a unit).")
        if not _int(mrp) or mrp < 0:
            raise GSTError(f"MRP of {name} isn't right.")
        if mrp and price > mrp:
            raise GSTError(f"{name} can't be sold above its MRP.")
        if not _int(disc) or disc < 0:
            raise GSTError(f"Discount on {name} isn't right.")
        gh = rate_h(l.get("gst"))
        gross = round_div(price * qm, 1000)
        if disc >= gross:
            raise GSTError(f"Discount on {name} is more than its amount.")
        lines.append({"item": name, "qm": qm, "price": price, "mrp": mrp, "gst": _num(l.get("gst")), "gh": gh,
                      "gross": gross, "line_disc": disc, "share": 0})
    bd = bill.get("bill_disc") or 0
    if not _int(bd) or bd < 0:
        raise GSTError("Bill discount isn't right.")
    W = sum(l["gross"] - l["line_disc"] for l in lines)
    if bd >= W:
        raise GSTError("Bill discount is more than the bill.")
    if bd:
        given, rems = 0, []
        for i, l in enumerate(lines):
            w = l["gross"] - l["line_disc"]
            s = bd * w // W
            l["share"] = s
            given += s
            rems.append((-(bd * w - s * W), i))
        rems.sort()
        k = 0
        while given < bd:
            lines[rems[k][1]]["share"] += 1
            given += 1
            k += 1
    for l in lines:
        l["disc"] = l["line_disc"] + l["share"]
        l["amount"] = l["gross"] - l["disc"]
        if l["amount"] <= 0:
            raise GSTError(f"Amount of {l['item']} must be more than zero after discount.")
    order, groups = [], {}
    for i, l in enumerate(lines):
        if l["gh"] not in groups:
            groups[l["gh"]] = {"gst": l["gst"], "gh": l["gh"], "amount": 0, "idx": []}
            order.append(l["gh"])
        groups[l["gh"]]["amount"] += l["amount"]
        groups[l["gh"]]["idx"].append(i)
    taxes, total, taxable_total, cgst_total = [], 0, 0, 0
    for gh in order:
        g = groups[gh]
        half = round_div(g["amount"] * gh, 2 * (10000 + gh)) if gh else 0
        taxable = g["amount"] - 2 * half
        s = 0
        for j, i in enumerate(g["idx"]):
            ln = lines[i]
            ln["taxable"] = taxable - s if j == len(g["idx"]) - 1 else round_div(ln["amount"] * 10000, 10000 + gh)
            s += ln["taxable"]
        taxes.append({"gst": g["gst"], "amount": g["amount"], "taxable": taxable, "cgst": half, "sgst": half})
        total += g["amount"]
        taxable_total += taxable
        cgst_total += half
    amount = round_div(total, 100) * 100 if bill.get("round") else total
    mrp_total = savings = gst_lines = 0
    for l in lines:
        if l["gh"]:
            gst_lines += 1
        if l["mrp"]:
            m = round_div(l["mrp"] * l["qm"], 1000)
            mrp_total += m
            if m > l["amount"]:
                savings += m - l["amount"]
    buyer = bill.get("buyer") or {}
    registered = bool(str(buyer.get("gstin") or "").strip())
    if registered and not gstin_ok(buyer["gstin"]):
        raise GSTError(f"The customer's GSTIN {buyer['gstin']} isn't right. Check it.")
    if not registered and amount >= B2C_DETAILS_FROM and (not str(buyer.get("name") or "").strip()
                                                         or not str(buyer.get("address") or "").strip()):
        raise GSTError("For a bill of Rs 50,000 or more, enter the customer's name and address (GST rule 46).")
    if gst_lines == len(lines):
        title = "Tax Invoice"
    elif gst_lines == 0:
        title = "Bill of Supply"
    else:
        title = "Tax Invoice" if registered else "Invoice-cum-Bill of Supply"
    return {
        "lines": [{k: l[k] for k in ("item", "qm", "price", "mrp", "gst", "gross", "disc", "amount", "taxable")}
                  for l in lines],
        "taxes": taxes, "total": total, "round_off": amount - total, "amount": amount,
        "taxable": taxable_total, "cgst": cgst_total, "sgst": cgst_total, "mrp_total": mrp_total,
        "savings": savings, "b2b": registered, "title": title,
    }


_CH = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN = re.compile(r"^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]Z[0-9A-Z]$")


def gstin_ok(g):
    g = str(g or "").strip().upper()
    if not _GSTIN.match(g):
        return False
    s = 0
    for i in range(14):
        v = _CH.index(g[i]) * (2 if i % 2 else 1)
        s += v // 36 + v % 36
    return _CH[(36 - s % 36) % 36] == g[14]


def rs(p):
    neg, p = p < 0, abs(p)
    r, f = divmod(p, 100)
    s = str(r)
    out, rest = s[-3:], s[:-3]
    while len(rest) > 2:
        out = rest[-2:] + "," + out
        rest = rest[:-2]
    if rest:
        out = rest + "," + out
    return ("-" if neg else "") + f"{out}.{f:02d}"


def qty(qm):
    s = f"{qm / 1000:.3f}".rstrip("0").rstrip(".")
    return s


STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}


def compute_return(lines, round_=True):
    """A return of part of a bill. lines: [{item, qm, gst, amount}] where amount is the share (GST included)
    of the original line being returned. Same tax rules as compute(), on fixed amounts."""
    out, order, groups = [], [], {}
    for l in lines:
        gh = rate_h(l["gst"])
        x = {"item": l["item"], "qm": l["qm"], "price": l.get("price", 0), "mrp": l.get("mrp", 0), "gst": _num(l["gst"]),
             "gross": l["amount"], "disc": 0, "amount": l["amount"], "taxable": 0}
        if x["amount"] <= 0 or x["qm"] <= 0:
            raise GSTError(f"Nothing to return for {l['item']}.")
        out.append(x)
        if gh not in groups:
            groups[gh] = {"gst": x["gst"], "amount": 0, "idx": []}
            order.append(gh)
        groups[gh]["amount"] += x["amount"]
        groups[gh]["idx"].append(len(out) - 1)
    if not out:
        raise GSTError("Pick what is being returned.")
    taxes, total, taxable_total, cgst_total = [], 0, 0, 0
    for gh in order:
        g = groups[gh]
        half = round_div(g["amount"] * gh, 2 * (10000 + gh)) if gh else 0
        taxable = g["amount"] - 2 * half
        s = 0
        for j, i in enumerate(g["idx"]):
            ln = out[i]
            ln["taxable"] = taxable - s if j == len(g["idx"]) - 1 else round_div(ln["amount"] * 10000, 10000 + gh)
            s += ln["taxable"]
        taxes.append({"gst": g["gst"], "amount": g["amount"], "taxable": taxable, "cgst": half, "sgst": half})
        total += g["amount"]
        taxable_total += taxable
        cgst_total += half
    amount = round_div(total, 100) * 100 if round_ else total
    return {"lines": out, "taxes": taxes, "total": total, "round_off": amount - total, "amount": amount,
            "taxable": taxable_total, "cgst": cgst_total, "sgst": cgst_total, "mrp_total": 0, "savings": 0,
            "b2b": False, "title": "Credit Note"}
