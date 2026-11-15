"""Talking to TallyPrime over its XML/HTTP server (F1 Help > Settings > Connectivity: "TallyPrime acts as"
Server or Both, port 9000).

Dukaan never deletes or changes anything already in Tally. Every request passes check() just before it is
sent: plain reads, or the creation of new vouchers, and nothing else.
"""
import http.client
import re
import socket
import threading
import urllib.error
import urllib.request
from datetime import date
from xml.sax.saxutils import escape as _esc

import tally_parse as tp

_lock = threading.Lock()          # one request at a time: Tally handles them one by one anyway


class Unreachable(Exception):
    """Tally couldn't be reached at all (nothing was sent, or nothing came back)."""


class Unknown(Exception):
    """The request went out but no answer came back: Tally may or may not have acted on it."""


def esc(t):
    return _esc(str(t), {'"': "&quot;", "'": "&apos;"})


_BAD = re.compile(r"<(GUID|REMOTEID|MASTERID|ALTERID|VOUCHERKEY|VCHKEY|ISDELETED|ISCANCELLED|IMPORTDUPS|ISOPTIONAL)[\s>]"
                  r"|REMOTEID\s*=|VCHKEY\s*=|<TAGNAME|<TAGVALUE|<DELETE|<ALTER|<CANCEL|<TDL", re.I)


def check(xml):
    """Raises ValueError unless xml is a plain read, or only creates new vouchers."""
    u = xml.upper()
    m = re.search(r"<TALLYREQUEST>\s*([A-Z ]+?)\s*</TALLYREQUEST>", u)
    req = m.group(1) if m else ""
    if req == "EXPORT":
        if re.search(r'ACTION\s*=|<ACTION[\s>]|<IMPORT|<DELETE|<ALTER|<CANCEL|<EXECUTE|<ACTIONS?[\s>]', u):
            raise ValueError("Blocked: a read request that tries to act on Tally")
        return
    if req == "IMPORT DATA":
        rep = re.search(r"<REPORTNAME>\s*(.*?)\s*</REPORTNAME>", u)
        if not rep or rep.group(1) != "VOUCHERS":
            raise ValueError("Blocked: Dukaan only adds vouchers to Tally")
        actions = re.findall(r'ACTION\s*=\s*"([^"]*)"', u)
        objs = re.findall(r"<TALLYMESSAGE[^>]*>\s*<([A-Z.]+)", u)
        if not actions or any(a != "CREATE" for a in actions) or not objs or any(o != "VOUCHER" for o in objs) \
                or len(objs) != len(re.findall(r"<VOUCHER[\s>]", u)) or _BAD.search(xml):
            raise ValueError("Blocked: it would have changed or removed something already in Tally")
        return
    raise ValueError("Blocked: not a request Dukaan makes")


_CANCEL = re.compile(
    r'^<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC>'
    r'<REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>[^<]*</SVCURRENTCOMPANY></STATICVARIABLES>'
    r'</REQUESTDESC><REQUESTDATA><TALLYMESSAGE xmlns:UDF="TallyUDF">'
    r'<VOUCHER DATE="\d{1,2}-[A-Za-z]{3}-\d{4}" TAGNAME="Voucher Number" TAGVALUE="[^"<>]+" VCHTYPE="[^"<>]+" ACTION="Cancel">'
    r'<NARRATION>[^<]*\[dk:[A-Za-z0-9-]+\][^<]*</NARRATION></VOUCHER></TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>$')


def check_cancel(xml):
    """The one change Dukaan may make in Tally: cancel (not delete) a single voucher it made itself."""
    if not _CANCEL.match(xml):
        raise ValueError("Blocked: not a plain cancel of one Dukaan voucher")


def cancel_xml(company, day, vtype, number, narration):
    return ("<ENVELOPE><HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER><BODY><IMPORTDATA><REQUESTDESC>"
            f"<REPORTNAME>Vouchers</REPORTNAME><STATICVARIABLES><SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY></STATICVARIABLES>"
            '</REQUESTDESC><REQUESTDATA><TALLYMESSAGE xmlns:UDF="TallyUDF">'
            f'<VOUCHER DATE="{_tdate(day)}" TAGNAME="Voucher Number" TAGVALUE="{esc(number)}" VCHTYPE="{esc(vtype)}" ACTION="Cancel">'
            f"<NARRATION>{esc(narration)}</NARRATION></VOUCHER></TALLYMESSAGE></REQUESTDATA></IMPORTDATA></BODY></ENVELOPE>")


def send(url, xml, timeout=120, cancel=False):
    """POST xml to Tally; the answer as text."""
    if cancel:
        check_cancel(xml)
    else:
        check(xml)
    body = xml.encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "text/xml;charset=utf-8"})
    with _lock:
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise Unreachable(f"Tally answered HTTP {e.code}")
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise Unknown(f"Tally didn't answer in {timeout} seconds") from e
            raise Unreachable(f"Can't reach Tally at {url}: {e.reason}")
        except (ConnectionRefusedError, socket.gaierror) as e:
            raise Unreachable(f"Can't reach Tally at {url}: {e}")
        except (socket.timeout, TimeoutError) as e:
            raise Unknown(f"Tally didn't answer in {timeout} seconds") from e
        except (http.client.HTTPException, ConnectionError, OSError) as e:
            # the request went out, but the answer didn't come back
            raise Unknown(f"Tally's answer didn't arrive: {e}") from e
        try:
            raw = resp.read()
        except (socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            raise Unknown(f"Tally's answer was cut off: {e}") from e
    return tp.decode(raw)


def read(url, xml, timeout=300):
    text = send(url, xml, timeout)
    return tp.parse_xml(text.encode("utf-8"))


def _tdate(d):
    return date.fromisoformat(d).strftime("%-d-%b-%Y")


def _sv(company, frm=None, to=None):
    s = "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"
    if company:
        s += f"<SVCURRENTCOMPANY>{esc(company)}</SVCURRENTCOMPANY>"
    if frm:
        s += f'<SVFROMDATE TYPE="Date">{_tdate(frm)}</SVFROMDATE>'
    if to:
        s += f'<SVTODATE TYPE="Date">{_tdate(to)}</SVTODATE>'
    return s


def collection(cid, type_, fetch, company="", native=False):
    what = "<NATIVEMETHOD>*</NATIVEMETHOD>" if native else f"<FETCH>{fetch}</FETCH>"
    return (f"<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>{cid}</ID></HEADER>"
            f"<BODY><DESC><STATICVARIABLES>{_sv(company)}</STATICVARIABLES><TDL><TDLMESSAGE>"
            f'<COLLECTION NAME="{cid}" ISMODIFY="No"><TYPE>{type_}</TYPE>{what}</COLLECTION>'
            "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>")


def masters(account_type, company):
    """Tally's own "List of Accounts" export: full masters (HSN, GST details, price lists, aliases)."""
    return ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE><ID>List of Accounts</ID></HEADER>"
            f"<BODY><DESC><STATICVARIABLES>{_sv(company)}<ACCOUNTTYPE>{account_type}</ACCOUNTTYPE></STATICVARIABLES></DESC></BODY></ENVELOPE>")


def vouchers_on(company, day, fetch="VoucherNumber, VoucherTypeName, Narration, Date"):
    return ("<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE><ID>DKVouchers</ID></HEADER>"
            f"<BODY><DESC><STATICVARIABLES>{_sv(company, day, day)}</STATICVARIABLES><TDL><TDLMESSAGE>"
            f'<COLLECTION NAME="DKVouchers" ISMODIFY="No"><TYPE>Voucher</TYPE><FETCH>{fetch}</FETCH><FILTER>DKInPeriod</FILTER></COLLECTION>'
            '<SYSTEM TYPE="Formulae" NAME="DKInPeriod">$Date &gt;= ##SVFromDate AND $Date &lt;= ##SVToDate</SYSTEM>'
            "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>")


def import_result(text):
    """(created count, [errors]) from Tally's answer to an import."""
    created = re.search(r"<CREATED>\s*(\d+)", text)
    errs = [re.sub(r"\s+", " ", e).strip() for e in re.findall(r"<LINEERROR>([\s\S]*?)</LINEERROR>", text)]
    n_err = re.search(r"<ERRORS>\s*(\d+)", text)
    if not errs and n_err and int(n_err.group(1)) > 0:
        errs = ["Tally reported an error but gave no reason. Check Tally's screen (it may be showing a message)."]
    return (int(created.group(1)) if created else 0), errs


def vouchers_tagged(url, company, day):
    """{Dukaan tag: [{number, vtype, cancelled}]} for the vouchers on that day in Tally that carry a tag
    ([dk:...] in the narration), plus "_numbers": how many vouchers have each (vtype, number)."""
    root = read(url, vouchers_on(company, day, "VoucherNumber, VoucherTypeName, Narration, Date, IsCancelled"), timeout=180)
    found, numbers = {}, {}
    for v in root.iter("VOUCHER"):
        num = tp.txt(v, "VOUCHERNUMBER")
        vt = tp.txt(v, "VOUCHERTYPENAME") or v.get("VCHTYPE") or ""
        numbers[(vt, num)] = numbers.get((vt, num), 0) + 1
        for m in re.finditer(r"\[dk:([A-Za-z0-9-]+)\]", tp.txt(v, "NARRATION")):
            found.setdefault(m.group(1), []).append({"number": num, "vtype": vt,
                                                     "cancelled": tp.txt(v, "ISCANCELLED").lower() == "yes"})
    found["_numbers"] = numbers
    return found


def tags_on(url, company, day):
    """The Dukaan tags ([dk:...]) in the narration of every voucher on that day in Tally."""
    return {k for k in vouchers_tagged(url, company, day) if k != "_numbers"}


def ping(url):
    """Names of the companies open in Tally."""
    root = read(url, collection("DKCompanies", "Company", "Name, StateName, BooksFrom"), timeout=20)
    return [c["name"] for c in tp.parse_companies(root)]
