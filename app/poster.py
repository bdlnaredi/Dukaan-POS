"""The background worker: copies Tally's masters now and then, and sends waiting bills to Tally one by one.

A voucher whose answer never came back (Tally busy, network dropped) is not simply sent again: Dukaan first
looks in Tally for that voucher's tag ([dk:...] in the narration) and only sends it if it isn't there,
so a bill never goes into Tally twice.
"""
import json
import threading
import time
import traceback

import billing
import db
import masters
import tally

state = {"tally": "not set up", "ok_at": 0, "error": "", "refresh_at": 0, "refresh_error": "", "busy": ""}
_wake = threading.Event()
_run_lock = threading.Lock()


def settings():
    return db.get("settings", {}) or {}


def url():
    return (settings().get("tally_url") or "").strip()


def wake():
    _wake.set()


def refresh_now():
    """Copy masters from Tally (raises on failure)."""
    s = db.get("tally_setup", {}) or {}
    u = url()
    if not u:
        raise ValueError("Enter Tally's address first (Tally page).")
    company = s.get("company")
    if not company:
        names = tally.ping(u)
        if not names:
            raise ValueError("No company is open in Tally. Open your company in Tally and try again.")
        company = names[0]
        s["company"] = company
        db.put("tally_setup", s)
    with _run_lock:
        state["busy"] = "Copying items and ledgers from Tally"
        try:
            r = masters.refresh(u, company)
        finally:
            state["busy"] = ""
    state["refresh_at"] = time.time()
    state["refresh_error"] = ""
    return r


def _ensure_posts(b, setup, info):
    """The voucher rows for a bill, made from the current setup; ones already in Tally are kept as they are."""
    planned = billing.plan(b, setup, info)
    have = {r["seq"]: r for r in db.rows("SELECT * FROM post WHERE bill_uid=?", (b["uid"],))}
    with db.tx() as c:
        for seq, (kind, label, xml) in enumerate(planned, 1):
            tag = f"{b['uid']}-{seq}"
            old = have.get(seq)
            if old and old["status"] in ("done", "sending", "unknown"):
                continue
            tally.check(xml)
            if old:
                c.execute("UPDATE post SET kind=?, label=?, xml=?, status='queued', error='' WHERE id=?",
                          (kind, label, xml, old["id"]))
            else:
                c.execute("INSERT INTO post(bill_uid, seq, kind, label, tag, xml, status, created) VALUES(?,?,?,?,?,?, 'queued', ?)",
                          (b["uid"], seq, kind, label, tag, xml, time.time()))
        c.execute("DELETE FROM post WHERE bill_uid=? AND seq>? AND status IN ('queued', 'failed')", (b["uid"], len(planned)))


def _send_bill(b, u):
    """Send a bill's waiting vouchers in order. Returns False when Tally can't be reached (stop for now)."""
    for p in db.rows("SELECT * FROM post WHERE bill_uid=? ORDER BY seq", (b["uid"],)):
        if p["status"] == "done":
            continue
        if p["status"] == "failed":
            return True
        if p["status"] in ("unknown", "sending"):
            try:
                found = tally.tags_on(u, (db.get("tally_setup") or {})["company"], b["date"])
            except (tally.Unreachable, tally.Unknown) as e:
                state["error"] = str(e)
                return False
            if p["tag"] in found:
                with db.tx() as c:
                    c.execute("UPDATE post SET status='done', done=?, error='' WHERE id=?", (time.time(), p["id"]))
                continue
        with db.tx() as c:
            c.execute("UPDATE post SET status='sending', sent=?, tries=tries+1 WHERE id=?", (time.time(), p["id"]))
        try:
            text = tally.send(u, p["xml"], timeout=120)
        except tally.Unreachable as e:
            with db.tx() as c:
                c.execute("UPDATE post SET status='queued', error=? WHERE id=?", (str(e)[:300], p["id"]))
            state["error"] = str(e)
            return False
        except tally.Unknown as e:
            with db.tx() as c:
                c.execute("UPDATE post SET status='unknown', error=? WHERE id=?", (str(e)[:300], p["id"]))
            state["error"] = str(e)
            return False
        except ValueError as e:          # blocked by the safety check
            with db.tx() as c:
                c.execute("UPDATE post SET status='failed', error=? WHERE id=?", (str(e), p["id"]))
                c.execute("UPDATE bill SET tally='failed', tally_error=? WHERE uid=?", (str(e), b["uid"]))
            return True
        created, errs = tally.import_result(text)
        with db.tx() as c:
            if created >= 1 and not errs:
                c.execute("UPDATE post SET status='done', done=?, error='' WHERE id=?", (time.time(), p["id"]))
            else:
                msg = "; ".join(errs) or ("Tally didn't add it: " + text.strip()[:300])
                c.execute("UPDATE post SET status='failed', error=? WHERE id=?", (msg[:500], p["id"]))
                c.execute("UPDATE bill SET tally='failed', tally_error=? WHERE uid=?", (f"{p['label']}: {msg}"[:600], b["uid"]))
                return True
    if not db.one("SELECT 1 x FROM post WHERE bill_uid=? AND status<>'done'", (b["uid"],)):
        with db.tx() as c:
            c.execute("UPDATE bill SET tally='posted', posted=?, tally_error='' WHERE uid=?", (time.time(), b["uid"]))
    return True


def post_waiting():
    u = url()
    setup = db.get("tally_setup", {}) or {}
    if not u:
        state["tally"] = "not set up"
        return
    if not setup.get("confirmed"):
        state["tally"] = "waiting for the owner to confirm the Tally setup"
        return
    probs = masters.problems(setup)
    if probs:
        state["tally"] = "setup incomplete: " + probs[0]
        return
    info = db.get("tally_company_info", {}) or {}
    shop = settings().get("shop") or {}
    info = {**info, "state": shop.get("state") or info.get("state", "")}
    with _run_lock:
        bills = db.rows("""SELECT * FROM bill WHERE tally IN ('pending', 'sending') AND status='ok' AND flag=''
            AND calc IS NOT NULL ORDER BY created LIMIT 40""")
        for b in bills:
            state["busy"] = f"Sending {b['number']} to Tally"
            try:
                _ensure_posts(b, setup, info)
                with db.tx() as c:
                    c.execute("UPDATE bill SET tally='sending' WHERE uid=? AND tally='pending'", (b["uid"],))
                if not _send_bill(b, u):
                    state["tally"] = "can't reach Tally"
                    return
            except (KeyError, ValueError) as e:
                with db.tx() as c:
                    c.execute("UPDATE bill SET tally='failed', tally_error=? WHERE uid=?",
                              (f"Check the Tally setup: {e}"[:500], b["uid"]))
            finally:
                state["busy"] = ""
        state["tally"] = "ok"
        state["ok_at"] = time.time()
        state["error"] = ""


def retry(uid=None):
    """Try failed bills again (with the current setup)."""
    with db.tx() as c:
        q = "UPDATE bill SET tally='pending', tally_error='' WHERE tally='failed' AND status='ok'" + (" AND uid=?" if uid else "")
        c.execute(q, (uid,) if uid else ())
        q2 = "UPDATE post SET status='queued', error='' WHERE status='failed'" + (" AND bill_uid=?" if uid else "")
        c.execute(q2, (uid,) if uid else ())
    wake()


def loop():
    last_refresh = 0
    while True:
        try:
            every = max(5, int(settings().get("refresh_minutes") or 30)) * 60
            if url() and time.time() - last_refresh > every:
                last_refresh = time.time()
                try:
                    refresh_now()
                except Exception as e:     # noqa: BLE001 - shown on the Tally page
                    state["refresh_error"] = str(e)
            post_waiting()
            if time.localtime().tm_hour >= 2 and db.rows("SELECT 1 FROM bill LIMIT 1"):
                db.backup()
        except Exception:                  # noqa: BLE001
            traceback.print_exc()
            state["error"] = "Something went wrong sending to Tally (see the container log)."
        _wake.wait(20)
        _wake.clear()


def start():
    threading.Thread(target=loop, daemon=True, name="poster").start()
