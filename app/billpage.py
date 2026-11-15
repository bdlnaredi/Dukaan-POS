"""A bill as a web page: the customer's link from WhatsApp, and a full-size (A4) copy for printing."""
import html
import json

import db
import gst as G

MODE = {"cash": "Cash", "upi": "UPI", "card": "Card", "credit": "Credit"}


def shop():
    s = (db.get("settings", {}) or {}).get("shop") or {}
    info = db.get("tally_company_info", {}) or {}
    addr = s.get("address") or "\n".join(info.get("address") or [])
    return {"name": s.get("name") or info.get("name") or "My Shop", "address": addr,
            "phone": s.get("phone") or info.get("phone", ""), "gstin": (s.get("gstin") or info.get("gstin", "")).upper(),
            "state": s.get("state") or info.get("state", ""), "fssai": s.get("fssai", ""), "email": s.get("email") or info.get("email", ""),
            "footer": ((db.get("settings", {}) or {}).get("print") or {}).get("footer", "Thank you! Visit again.")}


def render(b):
    e = lambda x: html.escape(str(x or ""))
    s = shop()
    data, calc = json.loads(b["data"]), json.loads(b["calc"] or "null")
    sale = b["kind"] == "sale"
    title = (calc or {}).get("title") or "Tax Invoice"
    if not sale:
        title = "Credit Note"
    code = s["gstin"][:2] if s["gstin"] else ""
    pos = f"{s['state']}" + (f" ({code})" if code else "")
    buyer = data["buyer"]
    rows = ""
    for i, l in enumerate((calc or {}).get("lines", [])):
        src = data["lines"][i] if i < len(data["lines"]) else {}
        rows += (f"<tr><td>{i + 1}</td><td>{e(l['item'])}<div class=s>HSN {e(src.get('hsn'))}</div></td>"
                 f"<td class=r>{G.qty(l['qm'])} {e(src.get('unit'))}</td>"
                 f"<td class=r>{G.rs(l['price']) if l.get('price') else ''}</td><td class=r>{l['gst']:g}%</td>"
                 f"<td class=r>{G.rs(l['taxable'])}</td><td class=r>{G.rs(l['amount'])}</td></tr>")
    taxes = "".join(f"<tr><td>{t['gst']:g}%</td><td class=r>{G.rs(t['taxable'])}</td><td class=r>{G.rs(t['cgst'])}</td>"
                    f"<td class=r>{G.rs(t['sgst'])}</td><td class=r>{G.rs(t['cgst'] + t['sgst'])}</td></tr>"
                    for t in (calc or {}).get("taxes", []))
    if sale:
        pays = ", ".join(f"{MODE[p['mode']]} {G.rs(p['amount'])}" for p in data["payments"])
        due = (calc or {}).get("amount", 0) - sum(p["amount"] for p in data["payments"])
        if due > 0:
            pays = (pays + ", " if pays else "") + f"on credit {G.rs(due)}"
    else:
        pays = f"Refund: {MODE[data['refund']]}"
    cust = ""
    if buyer.get("ledger") or buyer.get("name") or buyer.get("gstin"):
        cust = (f"<div><b>Bill to:</b> {e(buyer.get('name') or buyer.get('ledger'))}"
                + (f"<br>{e(buyer['address'])}" if buyer.get("address") else "")
                + (f"<br>GSTIN: {e(buyer['gstin'])}" if buyer.get("gstin") else "")
                + (f"<br>Phone: {e(buyer['phone'])}" if buyer.get("phone") else "") + "</div>")
    cancelled = '<div class=void>CANCELLED</div>' if b["status"] == "cancelled" else ""
    orig = "" if sale else f"<div>Against bill <b>{e(data['orig_number'])}</b> dated {e(data['orig_date'])}</div>"
    amount = (calc or {}).get("amount", b["amount"])
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{e(title)} {e(b['number'])} - {e(s['name'])}</title><style>
body{{font:14px/1.4 system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#f4f4f5;color:#111}}
.p{{max-width:800px;margin:16px auto;background:#fff;padding:24px;border-radius:8px;position:relative}}
h1{{font-size:20px;margin:0}} .t{{text-align:center;font-weight:700;letter-spacing:.05em;margin:12px 0;border:1px solid #111;padding:4px}}
table{{width:100%;border-collapse:collapse;margin:8px 0}} td,th{{border-bottom:1px solid #ddd;padding:5px 4px;text-align:left;vertical-align:top}}
th{{font-size:12px;color:#555}} .r{{text-align:right}} .s{{font-size:11px;color:#666}} .g{{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.tot td{{font-weight:700;font-size:16px;border:0}} .void{{position:absolute;top:40%;left:0;right:0;text-align:center;font-size:64px;color:#c00;opacity:.25;transform:rotate(-20deg)}}
.f{{margin-top:16px;font-size:12px;color:#444}} button{{font:inherit;padding:8px 14px;margin:0 auto 16px;display:block}}
@media print{{body{{background:#fff}} .p{{margin:0;padding:0}} button{{display:none}}}}
</style></head><body><div class=p>{cancelled}
<h1>{e(s['name'])}</h1><div>{e(s['address']).replace(chr(10), '<br>')}</div>
<div>{('Phone: ' + e(s['phone'])) if s['phone'] else ''} {('· ' + e(s['email'])) if s['email'] else ''}</div>
<div><b>GSTIN: {e(s['gstin'])}</b>{(' · FSSAI: ' + e(s['fssai'])) if s['fssai'] else ''}</div>
<div class=t>{e(title).upper()}</div>
<div class=g><div><b>{'Bill' if sale else 'Credit note'} no:</b> {e(b['number'])}<br><b>Date:</b> {e(b['date'])} {e(b['time'])}{orig}</div>
<div><b>Place of supply:</b> {e(pos)}<br><b>Reverse charge:</b> No</div>{cust}</div>
<table><tr><th>#</th><th>Item</th><th class=r>Qty</th><th class=r>Price</th><th class=r>GST</th><th class=r>Taxable</th><th class=r>Amount</th></tr>{rows}</table>
<table><tr><th>GST rate</th><th class=r>Taxable value</th><th class=r>CGST</th><th class=r>SGST</th><th class=r>Total tax</th></tr>{taxes}</table>
<table>{'<tr><td>Round off</td><td class=r>' + G.rs(calc['round_off']) + '</td></tr>' if calc and calc['round_off'] else ''}
<tr class=tot><td>Total</td><td class=r>₹ {G.rs(amount)}</td></tr></table>
<div class=s>{G_words(amount)}</div><div>{e(pays)}</div>
{('<div>You saved ₹ ' + G.rs(calc['savings']) + ' on MRP</div>') if calc and calc.get('savings') else ''}
<div class=f>{e(s['footer'])}<br>This is a computer-generated {e(title.lower())}. For {e(s['name'])} (authorised signatory).</div>
</div><button onclick="print()">Print</button></body></html>"""


def G_words(p):
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
            "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    two = lambda n: ones[n] if n < 20 else tens[n // 10] + (" " + ones[n % 10] if n % 10 else "")
    three = lambda n: ((ones[n // 100] + " Hundred" + (" " if n % 100 else "")) if n >= 100 else "") + two(n % 100)
    r, ps = divmod(p, 100)
    cr, r = divmod(r, 10000000)
    lk, r = divmod(r, 100000)
    th, r = divmod(r, 1000)
    parts = [x for x in ((three(cr) + " Crore") if cr else "", (two(lk) + " Lakh") if lk else "",
                         (two(th) + " Thousand") if th else "", three(r) if r else "") if x]
    s = "Rupees " + (" ".join(parts) if parts else "Zero")
    if ps:
        s += " and " + two(ps) + " Paise"
    return s + " Only"
