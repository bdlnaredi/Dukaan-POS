"""Who can use Dukaan: the owner (password) and cashiers (name + PIN), each on their own device."""
import hashlib
import hmac
import os
import re
import secrets
import time

import db

SESSION_DAYS = 60
_fails = {}


def _hash(secret, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 120000).hex()
    return f"{salt}${h}"


def _check(secret, stored):
    try:
        salt, h = stored.split("$", 1)
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(_hash(secret, salt), stored)


def owner_ok(pw):
    stored = db.get("owner_pw")
    if stored:
        return _check(pw, stored)
    env = os.environ.get("APP_PASSWORD", "")
    return bool(env) and hmac.compare_digest(pw.encode(), env.encode())


def set_owner_password(old, new):
    if not owner_ok(old):
        raise PermissionError("The current password isn't right.")
    if len(new) < 6:
        raise ValueError("Use at least 6 characters for the password.")
    db.put("owner_pw", _hash(new))


def _throttle(key):
    now = time.time()
    f = [t for t in _fails.get(key, []) if now - t < 300]
    _fails[key] = f
    if len(f) >= 8:
        raise PermissionError("Too many wrong tries. Wait 5 minutes.")


def _failed(key):
    _fails.setdefault(key, []).append(time.time())


def login(d, ip):
    device = re.sub(r"[^A-Za-z0-9-]", "", str(d.get("device") or ""))[:40]
    if d.get("kind") == "owner":
        _throttle("owner:" + ip)
        if not owner_ok(str(d.get("password") or "")):
            _failed("owner:" + ip)
            raise PermissionError("That password isn't right.")
        who = {"cashier_id": None, "role": "owner", "name": "Owner"}
    else:
        name = str(d.get("name") or "")
        _throttle("pin:" + name)
        c = db.one("SELECT * FROM cashier WHERE name=? AND active=1", (name,))
        if not c or not _check(str(d.get("pin") or ""), c["pin"]):
            _failed("pin:" + name)
            raise PermissionError("Name or PIN isn't right.")
        who = {"cashier_id": c["id"], "role": "cashier", "name": c["name"]}
    tok = secrets.token_urlsafe(24)
    with db.tx() as c:
        c.execute("INSERT INTO session(token, cashier_id, role, name, device, created, seen) VALUES(?,?,?,?,?,?,?)",
                  (tok, who["cashier_id"], who["role"], who["name"], device, time.time(), time.time()))
        c.execute("DELETE FROM session WHERE seen < ?", (time.time() - SESSION_DAYS * 86400,))
    return tok, session(tok)


def session(tok):
    if not tok:
        return None
    s = db.one("SELECT * FROM session WHERE token=?", (tok,))
    if not s or s["seen"] < time.time() - SESSION_DAYS * 86400:
        return None
    if s["role"] == "cashier":
        c = db.one("SELECT * FROM cashier WHERE id=? AND active=1", (s["cashier_id"],))
        if not c:
            return None
        s.update({"name": c["name"], "can_return": bool(c["can_return"]), "can_discount": bool(c["can_discount"]),
                  "can_credit": bool(c["can_credit"])})
    else:
        s.update({"can_return": True, "can_discount": True, "can_credit": True})
    if time.time() - s["seen"] > 600:
        with db.tx() as c:
            c.execute("UPDATE session SET seen=? WHERE token=?", (time.time(), tok))
    return s


def logout(tok):
    with db.tx() as c:
        c.execute("DELETE FROM session WHERE token=?", (tok,))


def cashiers():
    return [{k: r[k] for k in ("id", "name", "active", "can_return", "can_discount", "can_credit")}
            for r in db.rows("SELECT * FROM cashier ORDER BY name")]


def save_cashier(d):
    name = re.sub(r"\s+", " ", str(d.get("name") or "")).strip()[:40]
    if not name:
        raise ValueError("Enter the cashier's name.")
    pin = str(d.get("pin") or "")
    if pin and not re.fullmatch(r"\d{4,8}", pin):
        raise ValueError("A PIN is 4 to 8 digits.")
    flags = [1 if d.get(k) else 0 for k in ("active", "can_return", "can_discount", "can_credit")]
    with db.tx() as c:
        if d.get("id"):
            c.execute("UPDATE cashier SET name=?, active=?, can_return=?, can_discount=?, can_credit=? WHERE id=?",
                      (name, *flags, d["id"]))
            if pin:
                c.execute("UPDATE cashier SET pin=? WHERE id=?", (_hash(pin), d["id"]))
            if not flags[0]:
                c.execute("DELETE FROM session WHERE cashier_id=?", (d["id"],))
        else:
            if not pin:
                raise ValueError("Give the new cashier a PIN.")
            if c.execute("SELECT 1 FROM cashier WHERE name=?", (name,)).fetchone():
                raise ValueError("There's already a cashier with that name.")
            c.execute("INSERT INTO cashier(name, pin, role, active, can_return, can_discount, can_credit, created) "
                      "VALUES(?, ?, 'cashier', ?, ?, ?, ?, ?)", (name, _hash(pin), *flags, time.time()))
