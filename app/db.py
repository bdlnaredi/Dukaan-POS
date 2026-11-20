"""Dukaan's own database (SQLite, in DATA_DIR). Tally's data is only ever copied in here; what Dukaan adds
(prices, barcodes, bills) lives here too and goes to Tally only as new vouchers."""
import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "dukaan.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS setting(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS ledger(name TEXT PRIMARY KEY, parent TEXT, chain TEXT, gstin TEXT, mobile TEXT,
  gst_head TEXT, closing REAL);
CREATE TABLE IF NOT EXISTS vtype(name TEXT PRIMARY KEY, parent TEXT, base TEXT);
CREATE TABLE IF NOT EXISTS godown(name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS unit(name TEXT PRIMARY KEY, decimals INT);
CREATE TABLE IF NOT EXISTS item(name TEXT PRIMARY KEY, parent TEXT, unit TEXT, hsn TEXT, gst REAL, gst_from TEXT,
  price INT, mrp INT, aliases TEXT, stock REAL, seen REAL);
CREATE TABLE IF NOT EXISTS item_local(name TEXT PRIMARY KEY, price INT, mrp INT, barcodes TEXT, loose INT, fav INT,
  hsn TEXT, gst REAL, hidden INT DEFAULT 0, updated REAL);
CREATE TABLE IF NOT EXISTS counter(id INTEGER PRIMARY KEY, name TEXT, series TEXT UNIQUE, device TEXT, claimed REAL,
  active INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS cashier(id INTEGER PRIMARY KEY, name TEXT UNIQUE, pin TEXT, role TEXT, active INT DEFAULT 1,
  can_return INT DEFAULT 0, can_discount INT DEFAULT 1, can_credit INT DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS session(token TEXT PRIMARY KEY, cashier_id INT, role TEXT, name TEXT, device TEXT,
  created REAL, seen REAL);
CREATE TABLE IF NOT EXISTS numbering(counter_id INT, fy TEXT, kind TEXT, last INT, PRIMARY KEY(counter_id, fy, kind));
CREATE TABLE IF NOT EXISTS bill(uid TEXT PRIMARY KEY, kind TEXT, number TEXT, fy TEXT, counter_id INT,
  cashier_id INT, cashier TEXT, date TEXT, time TEXT, created REAL, received REAL, data TEXT, calc TEXT,
  amount INT, status TEXT, flag TEXT DEFAULT '', link TEXT, orig_uid TEXT, tally TEXT DEFAULT 'pending',
  tally_error TEXT DEFAULT '', posted REAL, cancelled_by TEXT, cancel_reason TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS bill_no ON bill(kind, number);
CREATE INDEX IF NOT EXISTS bill_date ON bill(date);
CREATE TABLE IF NOT EXISTS post(id INTEGER PRIMARY KEY, bill_uid TEXT, seq INT, kind TEXT, label TEXT, tag TEXT UNIQUE,
  xml TEXT, status TEXT, tries INT DEFAULT 0, error TEXT DEFAULT '', created REAL, sent REAL, done REAL);
CREATE INDEX IF NOT EXISTS post_bill ON post(bill_uid);
CREATE TABLE IF NOT EXISTS held(id TEXT PRIMARY KEY, counter_id INT, cashier TEXT, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, at REAL, who TEXT, what TEXT);
CREATE TABLE IF NOT EXISTS batch(id INTEGER PRIMARY KEY, item TEXT, batch_no TEXT, mfd TEXT, exp TEXT, qm INT, mrp INT,
  note TEXT, added REAL, added_by TEXT, active INT DEFAULT 1);
CREATE INDEX IF NOT EXISTS batch_item ON batch(item);
CREATE TABLE IF NOT EXISTS batch_use(id INTEGER PRIMARY KEY, bill_uid TEXT, line INT, batch_id INT, qm INT);
CREATE INDEX IF NOT EXISTS batch_use_bill ON batch_use(bill_uid);
CREATE INDEX IF NOT EXISTS batch_use_batch ON batch_use(batch_id);
CREATE TABLE IF NOT EXISTS offer(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, data TEXT, start TEXT, end TEXT,
  active INT DEFAULT 1, created REAL);
CREATE TABLE IF NOT EXISTS loyalty(phone TEXT PRIMARY KEY, name TEXT, points INT DEFAULT 0, earned INT DEFAULT 0,
  redeemed INT DEFAULT 0, first REAL, updated REAL);
CREATE TABLE IF NOT EXISTS loyalty_log(id INTEGER PRIMARY KEY, phone TEXT, bill_uid TEXT, kind TEXT, points INT, at REAL, note TEXT);
CREATE INDEX IF NOT EXISTS loyalty_log_bill ON loyalty_log(bill_uid);
CREATE TABLE IF NOT EXISTS shift(id INTEGER PRIMARY KEY, counter_id INT, opened_by TEXT, opened_at REAL, opening INT,
  closed_by TEXT, closed_at REAL, counted INT, denoms TEXT, expected INT, diff INT, kept INT, note TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS link_reserve(order_id TEXT, number TEXT, item TEXT, qm INT, created REAL, PRIMARY KEY(order_id, item));
CREATE TABLE IF NOT EXISTS cash_move(id INTEGER PRIMARY KEY, shift_id INT, counter_id INT, at REAL, kind TEXT, amount INT,
  reason TEXT, by TEXT);
"""

# columns added after the first version (ALTER TABLE ADD COLUMN, once)
COLUMNS = [("item_local", "min_stock", "REAL"), ("bill", "cancelled_at", "REAL"), ("cashier", "ext", "TEXT")]

_local = threading.local()
write_lock = threading.RLock()


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


def init():
    with write_lock:
        conn().executescript(SCHEMA)
        for table, col, typ in COLUMNS:
            have = {r["name"] for r in conn().execute(f"PRAGMA table_info({table})")}
            if col not in have:
                conn().execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


class tx:
    """with db.tx() as c: ... - one write transaction (serialised across threads)."""
    def __enter__(self):
        write_lock.acquire()
        self.c = conn()
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, et, ev, tb):
        try:
            self.c.execute("ROLLBACK" if et else "COMMIT")
        finally:
            write_lock.release()
        return False


def rows(sql, args=()):
    return [dict(r) for r in conn().execute(sql, args)]


def one(sql, args=()):
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def get(key, default=None):
    r = conn().execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return default


def put(key, value, c=None):
    sql = "INSERT INTO setting(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    if c is not None:
        c.execute(sql, (key, json.dumps(value)))
    else:
        with tx() as c2:
            c2.execute(sql, (key, json.dumps(value)))


def log(who, what):
    with tx() as c:
        c.execute("INSERT INTO log(at, who, what) VALUES(?, ?, ?)", (time.time(), who, what[:500]))
        c.execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 5000 FROM log)")


def backup(keep=45):
    """A copy of the database for today in DATA_DIR/backups (kept for 45 days). Safe while the app runs."""
    import datetime
    import glob
    folder = os.path.join(DATA_DIR, "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"dukaan-{datetime.date.today().isoformat()}.db")
    if os.path.exists(dest):
        return None
    tmp = dest + ".tmp"
    out = sqlite3.connect(tmp)
    with write_lock:
        conn().backup(out)
    out.close()
    os.replace(tmp, dest)
    for old in sorted(glob.glob(os.path.join(folder, "dukaan-*.db")))[:-keep]:
        os.remove(old)
    return dest
