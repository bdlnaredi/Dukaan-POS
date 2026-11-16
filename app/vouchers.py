"""The Tally vouchers for a bill: always new vouchers (ACTION="Create"), never a change to anything in Tally.

A sale becomes one Sales voucher (the bill itself, with its number, items, CGST, SGST and round off), plus:
  - for a bill paid in more than one way (cash + UPI, say): a Contra moving the UPI or card part out of Cash;
  - for a credit (udhaar) bill with part paid now: a Receipt from the customer against the bill.
A return becomes one Credit Note voucher against the original bill.

Tally writes a debit as a negative amount with ISDEEMEDPOSITIVE Yes.
"""
from datetime import date

import gst as G
from tally import esc


def _a(paise):
    return f"{paise / 100:.2f}"


def _d(iso):
    return iso.replace("-", "")


def rate_for(taxable, qm):
    """Rate per unit (rupees, as text) so that quantity x rate gives the taxable value back exactly."""
    amount = taxable / 100
    q = qm / 1000
    for dp in (2, 3, 4, 5, 6):
        r = round(amount / q, dp)
        if round(q * r, 2) == round(amount, 2):
            break
    s = f"{r:.6f}".rstrip("0")
    whole, _, dec = s.partition(".")
    return whole + "." + dec.ljust(2, "0")


def _qty(qm, unit):
    return f" {G.qty(qm)} {esc(unit)}"


def _line(ledger, dr, extra="", tag="LEDGERENTRIES.LIST"):
    """dr > 0 a debit, dr < 0 a credit (paise)."""
    return (f"<{tag}><LEDGERNAME>{esc(ledger)}</LEDGERNAME><ISDEEMEDPOSITIVE>{'Yes' if dr > 0 else 'No'}</ISDEEMEDPOSITIVE>"
            f"{extra}<AMOUNT>{_a(-dr)}</AMOUNT></{tag}>")


def envelope(company, voucher):
    return ("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC>"
            f"<REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY></STATICVARIABLES>"
            '</REQUESTDESC><REQUESTDATA><TALLYMESSAGE xmlns:UDF="TallyUDF">' + voucher +
            "</TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")


def _head(vt, day, number, party, narration, extra=""):
    return (f'<VOUCHER VCHTYPE="{esc(vt)}" ACTION="Create" OBJVIEW="Invoice Voucher View">'
            f"<DATE>{_d(day)}</DATE><EFFECTIVEDATE>{_d(day)}</EFFECTIVEDATE><VOUCHERTYPENAME>{esc(vt)}</VOUCHERTYPENAME>"
            + (f"<VOUCHERNUMBER>{esc(number)}</VOUCHERNUMBER>" if number else "")
            + f"<PARTYLEDGERNAME>{esc(party)}</PARTYLEDGERNAME><PARTYNAME>{esc(party)}</PARTYNAME>"
            + f"<NARRATION>{esc(narration)}</NARRATION>" + extra)


def invoice(setup, company, info, *, sale, day, number, party, debtor, calc, units, buyer, narration,
            ref=None, batches=None):
    """A Sales voucher (sale=True) or Credit Note (sale=False) for calc (gst.compute's answer).
    party: the ledger debited for a sale (Cash, the UPI bank, or the customer); debtor: it's a customer ledger
    (then the bill is tracked bill-wise). ref: (number, date) of the original bill, for a return."""
    vt = setup["sales_vtype"] if sale else setup["return_vtype"]
    sign = 1 if sale else -1
    state = info.get("state") or ""
    extra = ""
    if ref:
        extra += f"<REFERENCE>{esc(ref[0])}</REFERENCE><REFERENCEDATE>{_d(ref[1])}</REFERENCEDATE>"
    if buyer.get("gstin"):
        extra += f"<PARTYGSTIN>{esc(buyer['gstin'].upper())}</PARTYGSTIN>"
    if buyer.get("address"):
        extra += '<ADDRESS.LIST TYPE="String">' + "".join(
            f"<ADDRESS>{esc(x.strip())}</ADDRESS>" for x in str(buyer["address"]).splitlines()[:4] if x.strip()) + "</ADDRESS.LIST>"
    if state:
        extra += f"<STATENAME>{esc(state)}</STATENAME><PLACEOFSUPPLY>{esc(state)}</PLACEOFSUPPLY>"
    extra += ("<COUNTRYOFRESIDENCE>India</COUNTRYOFRESIDENCE><PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>"
              "<ISINVOICE>Yes</ISINVOICE><VCHENTRYMODE>Item Invoice</VCHENTRYMODE>")
    extra += f"<BASICBUYERNAME>{esc(buyer.get('name') or party)}</BASICBUYERNAME>"
    body = ""
    bills = ""
    if debtor:
        if sale:
            bills = (f"<BILLALLOCATIONS.LIST><NAME>{esc(number)}</NAME><BILLTYPE>New Ref</BILLTYPE>"
                     f"<AMOUNT>{_a(-calc['amount'])}</AMOUNT></BILLALLOCATIONS.LIST>")
        else:
            bills = (f"<BILLALLOCATIONS.LIST><NAME>{esc(ref[0])}</NAME><BILLTYPE>Agst Ref</BILLTYPE>"
                     f"<AMOUNT>{_a(calc['amount'])}</AMOUNT></BILLALLOCATIONS.LIST>")
    body += _line(party, sign * calc["amount"], "<ISPARTYLEDGER>Yes</ISPARTYLEDGER>").replace(
        "</LEDGERENTRIES.LIST>", bills + "</LEDGERENTRIES.LIST>")
    deemed = "No" if sale else "Yes"
    godown = setup.get("godown") or "Main Location"
    for i, l in enumerate(calc["lines"]):
        unit = units.get(l["item"], "")
        q = _qty(l["qm"], unit)
        amt = _a(sign * l["taxable"])
        ledger = setup["sales_ledgers"][f"{l['gst']:g}"]
        # batch allocations: the line's batches (when batches are sent to Tally), the rest from Primary Batch
        parts = [(n, qm) for n, qm in (batches or {}).get(i, []) if n and qm > 0]
        rest = l["qm"] - sum(qm for _, qm in parts)
        if rest > 0 or not parts:
            parts.append(("Primary Batch", rest if parts else l["qm"]))
        ballocs, given = "", 0
        for k, (bn, qm) in enumerate(parts):
            share = l["taxable"] - given if k == len(parts) - 1 else G.round_div(l["taxable"] * qm, l["qm"])
            given += share
            bq = _qty(qm, unit)
            ballocs += (f"<BATCHALLOCATIONS.LIST><GODOWNNAME>{esc(godown)}</GODOWNNAME><BATCHNAME>{esc(bn)}</BATCHNAME>"
                        f"<AMOUNT>{_a(sign * share)}</AMOUNT><ACTUALQTY>{bq}</ACTUALQTY><BILLEDQTY>{bq}</BILLEDQTY></BATCHALLOCATIONS.LIST>")
        body += (f"<ALLINVENTORYENTRIES.LIST><STOCKITEMNAME>{esc(l['item'])}</STOCKITEMNAME>"
                 f"<ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE><RATE>{rate_for(l['taxable'], l['qm'])}/{esc(unit)}</RATE>"
                 f"<AMOUNT>{amt}</AMOUNT><ACTUALQTY>{q}</ACTUALQTY><BILLEDQTY>{q}</BILLEDQTY>" + ballocs +
                 f"<ACCOUNTINGALLOCATIONS.LIST><LEDGERNAME>{esc(ledger)}</LEDGERNAME><ISDEEMEDPOSITIVE>{deemed}</ISDEEMEDPOSITIVE>"
                 f"<AMOUNT>{amt}</AMOUNT></ACCOUNTINGALLOCATIONS.LIST></ALLINVENTORYENTRIES.LIST>")
    cg, sg = {}, {}
    for t in calc["taxes"]:
        if t["cgst"]:
            k = f"{t['gst']:g}"
            cg[setup["cgst"][k]] = cg.get(setup["cgst"][k], 0) + t["cgst"]
            sg[setup["sgst"][k]] = sg.get(setup["sgst"][k], 0) + t["sgst"]
    for led, x in list(cg.items()) + list(sg.items()):
        body += _line(led, -sign * x)
    if calc["round_off"]:
        body += _line(setup["round_off"], -sign * calc["round_off"])
    return envelope(company, _head(vt, day, number, party, narration, extra) + body + "</VOUCHER>")


def contra(setup, company, *, day, frm, to, amount, narration):
    """Money moved from one cash/bank ledger to another (Dr to, Cr from)."""
    vt = setup["contra_vtype"]
    v = (f'<VOUCHER VCHTYPE="{esc(vt)}" ACTION="Create" OBJVIEW="Accounting Voucher View">'
         f"<DATE>{_d(day)}</DATE><EFFECTIVEDATE>{_d(day)}</EFFECTIVEDATE><VOUCHERTYPENAME>{esc(vt)}</VOUCHERTYPENAME>"
         f"<NARRATION>{esc(narration)}</NARRATION>"
         + _line(to, amount, tag="ALLLEDGERENTRIES.LIST") + _line(frm, -amount, tag="ALLLEDGERENTRIES.LIST") + "</VOUCHER>")
    return envelope(company, v)


def receipt(setup, company, *, day, party, account, amount, bill_no, narration):
    """Money received from a customer against a bill (Dr account, Cr party)."""
    vt = setup["receipt_vtype"]
    bills = (f"<BILLALLOCATIONS.LIST><NAME>{esc(bill_no)}</NAME><BILLTYPE>Agst Ref</BILLTYPE>"
             f"<AMOUNT>{_a(amount)}</AMOUNT></BILLALLOCATIONS.LIST>")
    v = (f'<VOUCHER VCHTYPE="{esc(vt)}" ACTION="Create" OBJVIEW="Accounting Voucher View">'
         f"<DATE>{_d(day)}</DATE><EFFECTIVEDATE>{_d(day)}</EFFECTIVEDATE><VOUCHERTYPENAME>{esc(vt)}</VOUCHERTYPENAME>"
         f"<PARTYLEDGERNAME>{esc(party)}</PARTYLEDGERNAME><NARRATION>{esc(narration)}</NARRATION>"
         + f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{esc(party)}</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>"
         f"<ISPARTYLEDGER>Yes</ISPARTYLEDGER><AMOUNT>{_a(amount)}</AMOUNT>{bills}</ALLLEDGERENTRIES.LIST>"
         + _line(account, amount, tag="ALLLEDGERENTRIES.LIST") + "</VOUCHER>")
    return envelope(company, v)
