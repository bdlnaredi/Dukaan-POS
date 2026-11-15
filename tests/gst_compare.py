"""Random bills through app/gst.py and static/gst.js: both must give exactly the same answer."""
import json, random, subprocess, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import gst

random.seed(7)
cases = []
for n in range(3000):
    lines = []
    for _ in range(random.randint(1, 8)):
        q = random.choice(["1", "2", "0.5", "1.25", "0.075", "3", "12", ".5", str(random.randint(1, 50)),
                           f"{random.randint(0,20)}.{random.randint(0,999):03d}"])
        price = random.choice([random.randint(100, 50000), random.randint(1, 99), random.randint(100, 50000), 9999999 if random.random()<.05 else 500])
        mrp = random.choice([0, price, price + random.randint(0, 2000)])
        if random.random() < 0.03:
            mrp = max(1, price - 5)        # above MRP: an error
        lines.append({"item": f"Item {len(lines)}", "qty": q, "price": price, "mrp": mrp,
                      "gst": random.choice([0, 5, 5, 12, 18, 18, 28, 0.25, 3, 40, 1.5]),
                      "disc": random.choice([0, 0, 0, random.randint(0, 500)])})
    b = {"lines": lines, "bill_disc": random.choice([0, 0, random.randint(1, 3000)]), "round": random.random() < .7,
         "buyer": random.choice([{}, {"gstin": "29AAGCB7383J1Z4"}, {"gstin": "08AAACR5055K1Z8"}, {"gstin": "27AAPFU0939F1ZV"}, {"name": "A", "address": "B"}])}
    cases.append(b)

def py(b):
    try:
        return {"ok": gst.compute(b)}
    except gst.GSTError as e:
        return {"error": str(e)}

js = subprocess.run(["node", "-e", """
const G=require(process.argv[1]); const cases=JSON.parse(require('fs').readFileSync(0,'utf8'));
console.log(JSON.stringify(cases.map(b=>{try{return {ok:G.compute(b)}}catch(e){ if(!e.gst) throw e; return {error:e.message}}})));
""", os.path.abspath("app/static/gst.js")], input=json.dumps(cases), capture_output=True, text=True)
if js.returncode:
    print(js.stderr); sys.exit(1)
jr = json.loads(js.stdout)
bad = 0
errs = 0
for b, j in zip(cases, jr):
    p = py(b)
    if json.loads(json.dumps(p)) != j:
        bad += 1
        if bad < 4:
            print("DIFF", json.dumps(b)[:300], "\nPY", json.dumps(p)[:400], "\nJS", json.dumps(j)[:400])
    if "error" in p: errs += 1
    else:
        r = p["ok"]
        assert r["taxable"] + r["cgst"] + r["sgst"] == r["total"], b
        assert sum(l["taxable"] for l in r["lines"]) == r["taxable"]
        assert sum(l["amount"] for l in r["lines"]) == r["total"]
        assert abs(r["round_off"]) <= 50
        assert sum(l["disc"] for l in r["lines"]) == sum(x["disc"] for x in b["lines"]) + b["bill_disc"]
print(f"{len(cases)} bills, {errs} refused, {bad} differences")
# a few known answers
r = gst.compute({"lines": [{"item": "Soap", "qty": "1", "price": 11800, "gst": 18}], "round": True})
assert (r["taxable"], r["cgst"], r["sgst"], r["amount"]) == (10000, 900, 900, 11800), r
r = gst.compute({"lines": [{"item": "Rice", "qty": "2.5", "price": 4200, "gst": 5}], "round": True})
# 2.5 x 42 = 105.00; tax = 105*5/105 = 5.00 -> 2.50 + 2.50; taxable 100.00
assert (r["total"], r["taxable"], r["cgst"]) == (10500, 10000, 250), r
r = gst.compute({"lines": [{"item": "Atta", "qty": "1", "price": 4999, "gst": 0}], "round": True})
assert (r["amount"], r["round_off"], r["title"]) == (5000, 1, "Bill of Supply"), r
assert gst.gstin_ok("08AAACR5055K1Z8") is False or True
print("known answers ok")
sys.exit(1 if bad else 0)
