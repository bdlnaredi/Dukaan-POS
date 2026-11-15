"""A stand-in for TallyPrime's XML server, for tests. Answers the exports Dukaan makes with a small grocery
company, and checks every voucher Dukaan imports the way Tally would: known voucher type, ledgers, items
and godown; a unique voucher number; debits equal to credits; quantity x rate = amount on every item line."""
import json
import re
import sys
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CO = "Shree Ganesh Kirana"
GROUPS = [("Sundry Debtors", "Current Assets"), ("Current Assets", ""), ("Cash-in-Hand", "Current Assets"),
          ("Bank Accounts", "Current Assets"), ("Sales Accounts", ""), ("Duties & Taxes", "Current Liabilities"),
          ("Current Liabilities", ""), ("Indirect Expenses", ""), ("Retail Customers", "Sundry Debtors")]
LEDGERS = [("Cash", "Cash-in-Hand", "", ""), ("HDFC Bank UPI", "Bank Accounts", "", ""), ("SBI Card Machine", "Bank Accounts", "", ""),
           ("Sales GST 5%", "Sales Accounts", "", ""), ("Sales GST 18%", "Sales Accounts", "", ""),
           ("Sales GST 12%", "Sales Accounts", "", ""), ("Sales Exempt", "Sales Accounts", "", ""),
           ("Output CGST 2.5%", "Duties & Taxes", "", "Central Tax"), ("Output SGST 2.5%", "Duties & Taxes", "", "State Tax"),
           ("Output CGST 9%", "Duties & Taxes", "", "Central Tax"), ("Output SGST 9%", "Duties & Taxes", "", "State Tax"),
           ("Output CGST 6%", "Duties & Taxes", "", "Central Tax"), ("Output SGST 6%", "Duties & Taxes", "", "State Tax"),
           ("Round Off", "Indirect Expenses", "", ""), ("Sharma Tea Stall", "Retail Customers", "08AAACR5055K1Z7", ""),
           ("Ramesh Kumar", "Sundry Debtors", "", "")]
ITEMS = [  # name, group, unit, hsn, gst, price, mrp, alias(barcode)
    ("Tata Salt 1kg", "Grocery", "Pcs", "25010020", 5, 2800, 2800, "8904043901015"),
    ("Aashirvaad Atta 5kg", "Grocery", "Pcs", "11010000", 5, 26500, 28000, "8901725181222"),
    ("Basmati Rice Loose", "Grocery", "Kg", "10063020", 5, 9000, 0, ""),
    ("Surf Excel 1kg", "Detergent", "Pcs", "34022010", 18, 14500, 15000, "8901030865299"),
    ("Moong Dal Loose", "Grocery", "Kg", "07133100", 0, 13000, 0, ""),
    ("Parle-G 250g", "Biscuits", "Pcs", "19053100", 18, 2500, 2500, "8901719110016"),
    ("Amul Ghee 1L", "Dairy", "Pcs", "04059020", 12, 61000, 65000, "8901262150019"),
    ("Mystery Item", "Grocery", "Pcs", "", None, 1000, 0, ""),
]
VTYPES = [("Sales", ""), ("Counter Sales", "Sales"), ("Credit Note", ""), ("Receipt", ""), ("Contra", ""), ("Payment", "")]

state = {"vouchers": [], "fail_next": "", "drop_after_create": 0, "imports": 0}
lock = threading.Lock()


def e(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def export(x):
    if "<TYPE>Company</TYPE>" in x:
        return (f"<ENVELOPE><BODY><DATA><COLLECTION><COMPANY NAME=\"{CO}\"><NAME>{CO}</NAME><STATENAME>Rajasthan</STATENAME>"
                "<GSTREGISTRATIONNUMBER>08ABCPN1234F1Z5</GSTREGISTRATIONNUMBER><ADDRESS.LIST><ADDRESS>Main Bazaar</ADDRESS>"
                "<ADDRESS>Ajeetgarh</ADDRESS></ADDRESS.LIST><PHONENUMBER>9876543210</PHONENUMBER></COMPANY></COLLECTION></DATA></BODY></ENVELOPE>")
    if "<TYPE>TaxUnit</TYPE>" in x:
        return "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"
    if "<TYPE>Group</TYPE>" in x:
        return "<ENVELOPE><BODY><DATA><COLLECTION>" + "".join(
            f'<GROUP NAME="{e(n)}"><PARENT>{e(p)}</PARENT></GROUP>' for n, p in GROUPS) + "</COLLECTION></DATA></BODY></ENVELOPE>"
    if "<TYPE>Ledger</TYPE>" in x:
        return "<ENVELOPE><BODY><DATA><COLLECTION>" + "".join(
            f'<LEDGER NAME="{e(n)}"><PARENT>{e(p)}</PARENT><PARTYGSTIN>{g}</PARTYGSTIN><GSTDUTYHEAD>{h}</GSTDUTYHEAD>'
            f'<CLOSINGBALANCE>{"-1500.00" if n == "Ramesh Kumar" else "0"}</CLOSINGBALANCE></LEDGER>'
            for n, p, g, h in LEDGERS) + "</COLLECTION></DATA></BODY></ENVELOPE>"
    if "<TYPE>VoucherType</TYPE>" in x:
        return "<ENVELOPE><BODY><DATA><COLLECTION>" + "".join(
            f'<VOUCHERTYPE NAME="{e(n)}"><PARENT>{e(p)}</PARENT></VOUCHERTYPE>' for n, p in VTYPES) + "</COLLECTION></DATA></BODY></ENVELOPE>"
    if "<TYPE>Godown</TYPE>" in x:
        return '<ENVELOPE><BODY><DATA><COLLECTION><GODOWN NAME="Main Location"/><GODOWN NAME="Shop"/><GODOWN NAME="Back Store"/></COLLECTION></DATA></BODY></ENVELOPE>'
    if "<TYPE>Unit</TYPE>" in x:
        return ('<ENVELOPE><BODY><DATA><COLLECTION><UNIT NAME="Pcs"><DECIMALPLACES>0</DECIMALPLACES></UNIT>'
                '<UNIT NAME="Kg"><DECIMALPLACES>3</DECIMALPLACES></UNIT></COLLECTION></DATA></BODY></ENVELOPE>')
    if "<TYPE>StockItem</TYPE>" in x:
        return "<ENVELOPE><BODY><DATA><COLLECTION>" + "".join(
            f'<STOCKITEM NAME="{e(n)}"><PARENT>{g}</PARENT><BASEUNITS>{u}</BASEUNITS><CLOSINGBALANCE> 40 {u}</CLOSINGBALANCE>'
            f'<CLOSINGRATE>{p / 100 * .8:.2f}/{u}</CLOSINGRATE></STOCKITEM>' for n, g, u, h, r, p, m, a in ITEMS) + "</COLLECTION></DATA></BODY></ENVELOPE>"
    if "<ACCOUNTTYPE>Stock Items</ACCOUNTTYPE>" in x:
        out = "<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA>"
        for n, g, u, h, r, p, m, a in ITEMS:
            gst = ""
            if h or r is not None:
                gst = (f"<GSTDETAILS.LIST><APPLICABLEFROM>20170701</APPLICABLEFROM><HSNCODE>{h}</HSNCODE>"
                       f"<TAXABILITY>{'Exempt' if r == 0 else 'Taxable'}</TAXABILITY>"
                       + (f"<STATEWISEDETAILS.LIST><RATEDETAILS.LIST><GSTRATEDUTYHEAD>IGST</GSTRATEDUTYHEAD><GSTRATE>{r}</GSTRATE>"
                          f"</RATEDETAILS.LIST></STATEWISEDETAILS.LIST>" if r else "") + "</GSTDETAILS.LIST>")
            out += (f'<TALLYMESSAGE><STOCKITEM NAME="{e(n)}"><NAME.LIST><NAME>{e(n)}</NAME>' + (f"<NAME>{a}</NAME>" if a else "")
                    + f"</NAME.LIST><PARENT>{g}</PARENT>{gst}<STANDARDPRICELIST.LIST><DATE>20260401</DATE><RATE>{p / 100:.2f}/{u}</RATE>"
                    f"</STANDARDPRICELIST.LIST>" + (f"<MRPDETAILS.LIST><MRPRATEDETAILS.LIST><MRPRATE>{m / 100:.2f}/{u}</MRPRATE></MRPRATEDETAILS.LIST></MRPDETAILS.LIST>" if m else "")
                    + "</STOCKITEM></TALLYMESSAGE>")
        return out + "</REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    if "<ACCOUNTTYPE>Stock Groups</ACCOUNTTYPE>" in x:
        return "<ENVELOPE><BODY><IMPORTDATA><REQUESTDATA></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>"
    if "<TYPE>Voucher</TYPE>" in x:
        d = re.search(r'<SVFROMDATE TYPE="Date">([^<]+)<', x).group(1)
        with lock:
            vs = [v for v in state["vouchers"] if v["tdate"] == d]
        return "<ENVELOPE><BODY><DATA><COLLECTION>" + "".join(
            f"<VOUCHER><VOUCHERNUMBER>{e(v['number'])}</VOUCHERNUMBER><NARRATION>{e(v['narration'])}</NARRATION></VOUCHER>"
            for v in vs) + "</COLLECTION></DATA></BODY></ENVELOPE>"
    return "<ENVELOPE><BODY><DATA></DATA></BODY></ENVELOPE>"


def amt(el):
    t = (el.findtext("AMOUNT") or "0").strip()
    return round(float(t) * 100)


def check_voucher(v):
    ledgers = {n for n, *_ in LEDGERS}
    items = {n: u for n, g, u, *_ in ITEMS}
    vt = v.get("VCHTYPE")
    if vt not in {n for n, _ in VTYPES}:
        return f"Voucher type '{vt}' does not exist"
    total = 0
    for tag in ("LEDGERENTRIES.LIST", "ALLLEDGERENTRIES.LIST"):
        for l in v.findall(tag):
            n = l.findtext("LEDGERNAME")
            if n not in ledgers:
                return f"Ledger '{n}' does not exist!"
            a = amt(l)
            deemed = l.findtext("ISDEEMEDPOSITIVE") == "Yes"
            if (a < 0) != deemed and a != 0:
                return f"ISDEEMEDPOSITIVE doesn't match the sign on {n}"
            ba = sum(amt(b) for b in l.findall("BILLALLOCATIONS.LIST"))
            if l.findall("BILLALLOCATIONS.LIST") and ba != a:
                return f"Bill allocations on {n} ({ba}) don't add up to {a}"
            total += a
    for it in v.findall("ALLINVENTORYENTRIES.LIST"):
        n = it.findtext("STOCKITEMNAME")
        if n not in items:
            return f"Stock item '{n}' does not exist!"
        a = amt(it)
        q = it.findtext("ACTUALQTY").strip()
        qn, qu = q.split()
        if qu != items[n]:
            return f"Unit {qu} isn't {n}'s unit"
        rate = it.findtext("RATE")
        r, ru = rate.split("/")
        if round(abs(float(qn) * float(r)) * 100) != abs(a):
            return f"{n}: {qn} x {r} isn't {a / 100}"
        g = it.find("BATCHALLOCATIONS.LIST")
        if g.findtext("GODOWNNAME") not in ("Main Location", "Shop", "Back Store"):
            return "Godown does not exist"
        if amt(g) != a or amt(it.find("ACCOUNTINGALLOCATIONS.LIST")) != a:
            return "Allocations don't match the item amount"
        if it.find("ACCOUNTINGALLOCATIONS.LIST").findtext("LEDGERNAME") not in ledgers:
            return "Sales ledger does not exist"
        total += a
    if total != 0:
        return f"Voucher totals do not match! Dr/Cr differ by {total / 100:.2f}"
    num = v.findtext("VOUCHERNUMBER")
    if num and any(x["number"] == num and x["vtype"] == vt for x in state["vouchers"]):
        return f"Duplicate Voucher Number '{num}'"
    return ""


def import_(x):
    root = ET.fromstring(x)
    vs = list(root.iter("VOUCHER"))
    with lock:
        state["imports"] += 1
        if state["fail_next"]:
            msg, state["fail_next"] = state["fail_next"], ""
            return f"<RESPONSE><CREATED>0</CREATED><ERRORS>1</ERRORS><LINEERROR>{msg}</LINEERROR></RESPONSE>", False
        created, errs = 0, []
        for v in vs:
            err = check_voucher(v)
            if err:
                errs.append(err)
                continue
            d = v.findtext("DATE")
            from datetime import date
            td = date(int(d[:4]), int(d[4:6]), int(d[6:])).strftime("%-d-%b-%Y")
            state["vouchers"].append({"vtype": v.get("VCHTYPE"), "number": v.findtext("VOUCHERNUMBER") or "",
                                      "narration": v.findtext("NARRATION") or "", "tdate": td, "xml": ET.tostring(v).decode()})
            created += 1
        drop = state["drop_after_create"] > 0 and created
        if drop:
            state["drop_after_create"] -= 1
    body = f"<RESPONSE><CREATED>{created}</CREATED><ALTERED>0</ALTERED><ERRORS>{len(errs)}</ERRORS>" + \
        "".join(f"<LINEERROR>{e(m)}</LINEERROR>" for m in errs) + "</RESPONSE>"
    return body, drop


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        x = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if self.path == "/control":
            with lock:
                state.update(json.loads(x))
            return self._ok("{}")
        if "<TALLYREQUEST>Import Data" in x:
            body, drop = import_(x)
            if drop:                       # Tally did it, but the answer never arrives
                self.connection.close()
                return
            return self._ok(body)
        return self._ok(export(x))

    def do_GET(self):
        with lock:
            return self._ok(json.dumps({"vouchers": state["vouchers"], "imports": state["imports"]}))

    def _ok(self, s):
        b = s.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
