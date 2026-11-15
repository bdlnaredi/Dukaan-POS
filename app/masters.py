"""Copying Tally's masters (ledgers, stock items with HSN and GST, voucher types, godowns, units, company
details) into Dukaan. Reads only. Also makes first guesses for the Tally setup (which ledgers bills go to),
which the owner then checks and confirms."""
import json
import re
import time

import db
import tally
import tally_parse as tp

BASES = ("Sales", "Credit Note", "Receipt", "Contra", "Payment", "Journal", "Purchase", "Debit Note")


def _chains(groups):
    parent = {g["name"]: g["parent"] for g in groups}
    out = {}
    for g in parent:
        seen, cur, path = set(), g, []
        while cur and cur not in seen:
            seen.add(cur)
            path.append(cur)
            cur = parent.get(cur, "")
        out[g] = "|" + "|".join(reversed(path)) + "|"
    return out


def _base(vtypes):
    parent = {v["name"]: v["parent"] for v in vtypes}
    out = {}
    for n in parent:
        cur, seen = n, set()
        while cur and cur not in BASES and cur not in seen:
            seen.add(cur)
            cur = parent.get(cur, "")
        out[n] = cur if cur in BASES else ""
    return out


def _latest(blocks):
    return sorted(blocks, key=lambda x: tp.txt(x, "APPLICABLEFROM") or tp.txt(x, "DATE"))[-1] if blocks else None


def _paise(text):
    """'120.00/Kg' -> 12000"""
    return round(tp.num(text) * 100) if text else 0


def _gst_of(el):
    """(hsn, gst rate or None) set on a stock item or stock group in Tally."""
    hsn = ""
    for path in ("HSNDETAILS.LIST", "GSTDETAILS.LIST"):
        b = _latest(el.findall(path))
        if b is not None:
            hsn = tp.txt(b, "HSNCODE") or tp.txt(b, "HSN")
            if hsn:
                break
    gst = None
    b = _latest(el.findall("GSTDETAILS.LIST"))
    if b is not None:
        taxability = tp.txt(b, "TAXABILITY").lower()
        for r in b.iter("RATEDETAILS.LIST"):
            head = tp.txt(r, "GSTRATEDUTYHEAD").lower()
            if head in ("igst", "integrated tax") and tp.txt(r, "GSTRATE"):
                gst = tp.num(tp.txt(r, "GSTRATE"))
                break
        if gst is None and taxability in ("exempt", "nil rated"):
            gst = 0.0
    return (hsn or tp.txt(el, "HSNCODE")), gst


def _items_extra(root, tag):
    out = {}
    for el in root.iter(tag):
        n = tp.name_of(el)
        if not n:
            continue
        hsn, gst = _gst_of(el)
        names = [x.text.strip() for x in el.findall("NAME.LIST/NAME") if x.text and x.text.strip()]
        price = 0
        pl = el.findall("STANDARDPRICELIST.LIST")
        b = _latest(pl)
        if b is not None:
            price = _paise(tp.txt(b, "RATE"))
        mrp = 0
        for m in el.iter("MRPRATEDETAILS.LIST"):
            mrp = _paise(tp.txt(m, "MRPRATE")) or mrp
        out[n] = {"hsn": hsn, "gst": gst, "aliases": [a for a in names if a != n], "price": price, "mrp": mrp,
                  "parent": tp.clean_parent(tp.txt(el, "PARENT"))}
    return out


def refresh(url, company):
    """Copy everything Dukaan needs from Tally. Returns a short summary."""
    t0 = time.time()
    rd = lambda x, timeout=300: tally.read(url, x, timeout)
    groups = tp.parse_groups(rd(tally.collection("DKGroups", "Group", "Name, Parent", company)))
    ledgers = tp.parse_ledgers(rd(tally.collection(
        "DKLedgers", "Ledger", "Name, Parent, ClosingBalance, LedgerMobile, PartyGSTIN, TaxType, GSTDutyHead", company)))
    vtypes = tp.parse_vtypes(rd(tally.collection("DKVchTypes", "VoucherType", "Name, Parent", company)))
    godowns = [tp.name_of(el) for el in rd(tally.collection("DKGodowns", "Godown", "Name", company)).iter("GODOWN")]
    units = {}
    for el in rd(tally.collection("DKUnits", "Unit", "Name, DecimalPlaces", company)).iter("UNIT"):
        if tp.name_of(el):
            units[tp.name_of(el)] = int(tp.num(tp.txt(el, "DECIMALPLACES")))
    stock = tp.parse_stock(rd(tally.collection("DKStock", "StockItem", "Name, Parent, BaseUnits, ClosingBalance, ClosingRate", company)))
    extra = _items_extra(rd(tally.masters("Stock Items", company), 600), "STOCKITEM")
    sgroups = _items_extra(rd(tally.masters("Stock Groups", company)), "STOCKGROUP")
    info = {}
    try:
        info = tp.parse_company_info(rd(tally.collection(
            "DKCompany", "Company", "Name, Address, StateName, PinCode, Email, PhoneNumber, IncomeTaxNumber, GSTRegistrationNumber",
            company))).get(company, {})
    except (tally.Unreachable, tally.Unknown, ValueError):
        pass
    try:
        tu = tp.parse_tax_units(rd(tally.collection("DKTaxUnits", "TaxUnit", "", company, native=True)))
        for k, v in tu.items():
            info.setdefault(k, v)
            if k == "gstin":
                info[k] = v
    except (tally.Unreachable, tally.Unknown, ValueError):
        pass

    chains = _chains(groups)
    base = _base(vtypes)
    now = time.time()

    def group_gst(g, seen=()):
        """HSN and GST set on the item's stock group, or the group above it."""
        x = sgroups.get(g)
        if not x or g in seen:
            return "", None
        hsn, gst = x["hsn"], x["gst"]
        if (not hsn or gst is None) and x["parent"]:
            h2, g2 = group_gst(x["parent"], seen + (g,))
            hsn, gst = hsn or h2, gst if gst is not None else g2
        return hsn, gst

    with db.tx() as c:
        c.execute("DELETE FROM ledger")
        c.executemany("INSERT OR REPLACE INTO ledger(name, parent, chain, gstin, mobile, gst_head, closing) VALUES(?,?,?,?,?,?,?)",
                      [(l["name"], l["parent"], chains.get(l["parent"], "|" + l["parent"] + "|") + l["name"] + "|",
                        (l["gstin"] or "").upper(), l["mobile"] or l["phone"], l["gst_head"], l["closing"]) for l in ledgers])
        c.execute("DELETE FROM vtype")
        c.executemany("INSERT OR REPLACE INTO vtype(name, parent, base) VALUES(?,?,?)",
                      [(v["name"], v["parent"], base.get(v["name"], "")) for v in vtypes])
        c.execute("DELETE FROM godown")
        c.executemany("INSERT OR REPLACE INTO godown(name) VALUES(?)", [(g,) for g in godowns if g])
        c.execute("DELETE FROM unit")
        c.executemany("INSERT OR REPLACE INTO unit(name, decimals) VALUES(?,?)", list(units.items()))
        for s in stock:
            x = extra.get(s["name"], {})
            hsn, gst, frm = x.get("hsn", ""), x.get("gst"), "item" if x.get("gst") is not None else ""
            if not hsn or gst is None:
                h2, g2 = group_gst(x.get("parent") or s["parent"])
                if not hsn:
                    hsn = h2
                if gst is None and g2 is not None:
                    gst, frm = g2, "group"
            c.execute("""INSERT INTO item(name, parent, unit, hsn, gst, gst_from, price, mrp, aliases, stock, seen)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET parent=excluded.parent, unit=excluded.unit,
                hsn=excluded.hsn, gst=excluded.gst, gst_from=excluded.gst_from, price=excluded.price, mrp=excluded.mrp,
                aliases=excluded.aliases, stock=excluded.stock, seen=excluded.seen""",
                      (s["name"], s["parent"], s["unit"], hsn, gst, frm, x.get("price", 0), x.get("mrp", 0),
                       json.dumps(x.get("aliases", [])), s["closing_qty"], now))
        c.execute("DELETE FROM item WHERE seen < ?", (now - 1,))       # gone from Tally
        db.put("tally_company_info", info, c)
        db.put("tally_refreshed", {"at": now, "company": company, "seconds": round(now - t0, 1),
                                   "items": len(stock), "ledgers": len(ledgers)}, c)
    guess_setup()
    return {"items": len(stock), "ledgers": len(ledgers), "seconds": round(time.time() - t0, 1)}


# ---------------------------------------------------------------- the Tally setup

def rates_in(name):
    """Rates written in a ledger's name: 'Output CGST 9%', 'Sales @18', 'GST Sales 5' -> {9.0} etc."""
    return {float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", name or "")} | \
           {float(x) for x in re.findall(r"@\s*(\d+(?:\.\d+)?)", name or "")}


def head_of(l):
    g = (l.get("gst_head") or "").lower()
    n = l["name"].lower()
    if "integrated" in g or "igst" in n:
        return "i"
    if "central" in g or "cgst" in n:
        return "c"
    if "state" in g or "sgst" in n or "utgst" in n:
        return "s"
    return ""


def choices():
    """What the owner can pick from in the Tally setup."""
    leds = db.rows("SELECT name, chain, gst_head FROM ledger ORDER BY name")
    vt = db.rows("SELECT name, base FROM vtype ORDER BY name")
    under = lambda *gs: [l["name"] for l in leds if any(f"|{g}|" in l["chain"] for g in gs)]
    taxes = [l for l in leds if "|Duties & Taxes|" in l["chain"]]
    return {
        "vtypes": {b: [v["name"] for v in vt if v["base"] == b] for b in ("Sales", "Credit Note", "Receipt", "Contra")},
        "cash": under("Cash-in-Hand"),
        "bank": under("Bank Accounts", "Bank OD A/c", "Cash-in-Hand"),
        "sales": under("Sales Accounts"),
        "cgst": [l["name"] for l in taxes if head_of(l) == "c"] or [l["name"] for l in taxes],
        "sgst": [l["name"] for l in taxes if head_of(l) == "s"] or [l["name"] for l in taxes],
        "other": [l["name"] for l in leds if not any(x in l["chain"] for x in ("|Sundry Debtors|", "|Sundry Creditors|"))],
        "godowns": [r["name"] for r in db.rows("SELECT name FROM godown ORDER BY name")] or ["Main Location"],
        "rates": sorted({r["gst"] for r in db.rows("SELECT DISTINCT COALESCE(l.gst, i.gst) gst FROM item i "
                                                     "LEFT JOIN item_local l ON l.name=i.name") if r["gst"] is not None}),
    }


def _pick(names, prefer=(), rate=None):
    if not names:
        return ""
    if rate is not None:
        exact = [n for n in names if rate in rates_in(n)]
        if exact:
            return exact[0]
        plain = [n for n in names if not rates_in(n)]
        if plain:
            names = plain
    for p in prefer:
        for n in names:
            if re.search(p, n, re.I):
                return n
    return names[0]


def guess_setup():
    """Fill in anything not yet set in the Tally setup with a likely ledger. Never changes what the owner set."""
    s = db.get("tally_setup", {}) or {}
    ch = choices()
    vt = ch["vtypes"]
    g = lambda k, v: s.get(k) or v
    s["sales_vtype"] = g("sales_vtype", _pick(vt["Sales"], (r"counter", r"pos\b", r"retail", r"^sales$")))
    s["return_vtype"] = g("return_vtype", _pick(vt["Credit Note"], (r"return", r"^credit note$")))
    s["receipt_vtype"] = g("receipt_vtype", _pick(vt["Receipt"], (r"^receipt$",)))
    s["contra_vtype"] = g("contra_vtype", _pick(vt["Contra"], (r"^contra$",)))
    s["cash"] = g("cash", _pick(ch["cash"], (r"^cash$", r"counter", r"cash")))
    banks = [b for b in ch["bank"] if b not in ch["cash"]]
    s["upi"] = g("upi", _pick(banks, (r"upi", r"gpay|phonepe|paytm", r"qr")))
    s["card"] = g("card", _pick(banks, (r"card", r"pos\b", r"swipe", r"edc")))
    s["round_off"] = g("round_off", _pick([n for n in ch["other"] if re.search(r"round", n, re.I)]))
    s["godown"] = g("godown", _pick(ch["godowns"], (r"^(shop|store|counter|dukaan)$", r"shop|counter|dukaan", r"^main location$")))
    for key, names, pref in (("sales_ledgers", ch["sales"], (r"gst|local|intra|state", r"sales")),
                             ("cgst", ch["cgst"], (r"output", r"cgst")), ("sgst", ch["sgst"], (r"output", r"sgst"))):
        m = dict(s.get(key) or {})
        for r in ch["rates"]:
            k = f"{r:g}"
            if r == 0 and key != "sales_ledgers":
                continue
            if not m.get(k):
                if key == "sales_ledgers" and r == 0:
                    m[k] = _pick(names, (r"exempt|nil|0\s*%", r"sales"))
                else:
                    m[k] = _pick(names, pref, r if key == "sales_ledgers" else r / 2)
        s[key] = m
    s.setdefault("confirmed", False)
    db.put("tally_setup", s)
    return s


def problems(setup=None):
    """What stops bills going to Tally with this setup: a list of plain messages."""
    s = setup or db.get("tally_setup", {}) or {}
    ch = choices()
    out = []
    leds = {r["name"] for r in db.rows("SELECT name FROM ledger")}
    vts = {r["name"] for r in db.rows("SELECT name FROM vtype")}
    if not s.get("company"):
        out.append("Pick the company in Tally.")
    for k, label in (("sales_vtype", "voucher type for bills"), ("return_vtype", "voucher type for returns"),
                     ("receipt_vtype", "voucher type for receipts"), ("contra_vtype", "voucher type for contra")):
        if s.get(k) not in vts:
            out.append(f"Pick the {label}.")
    for k, label in (("cash", "Cash ledger"), ("upi", "UPI ledger"), ("card", "card ledger"), ("round_off", "Round Off ledger")):
        if s.get(k) not in leds:
            out.append(f"Pick the {label}.")
    for r in ch["rates"]:
        k = f"{r:g}"
        if (s.get("sales_ledgers") or {}).get(k) not in leds:
            out.append(f"Pick the sales ledger for {k}% items.")
        if r and (s.get("cgst") or {}).get(k) not in leds:
            out.append(f"Pick the CGST ledger for {k}% items.")
        if r and (s.get("sgst") or {}).get(k) not in leds:
            out.append(f"Pick the SGST ledger for {k}% items.")
    return out
