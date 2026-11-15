/* Dukaan GST maths. The same rules are in app/gst.py; tests/gst_cases.json keeps the two in step.
 *
 * Money is whole paise and quantities are thousandths of a unit (integers), so the phone, the counter PC
 * and the server always get exactly the same figures.
 *
 * Prices are what the customer pays per unit, GST included (like a shelf price). For each GST rate on
 * the bill: tax = rate's share of the amount, split equally into CGST and SGST; taxable value = the rest.
 * Counter sales are always within the state (place of supply is the shop), so there is no IGST.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.GST = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var SLABS = [0, 0.1, 0.25, 1, 1.5, 3, 5, 6, 7.5, 12, 18, 28, 40];
  var B2C_DETAILS_FROM = 5000000; // Rule 46: name, address and state of an unregistered buyer from Rs 50,000

  function err(msg) { var e = new Error(msg); e.gst = true; return e; }

  // round(a / b) to the nearest whole number, halves up; a >= 0, b > 0
  function roundDiv(a, b) { return Math.floor((2 * a + b) / (2 * b)); }

  // "1.25" -> 1250 thousandths; at most 3 decimals
  function milli(q) {
    var s = String(q == null ? "" : q).trim();
    if (!/^\d+(\.\d{0,3})?$/.test(s) && !/^\.\d{1,3}$/.test(s)) throw err("Quantity " + s + " isn't a number with up to 3 decimals.");
    var parts = s.split(".");
    var whole = parts[0] ? parseInt(parts[0], 10) : 0;
    var frac = (parts[1] || "").padEnd(3, "0");
    return whole * 1000 + parseInt(frac, 10);
  }

  function rateH(g) { // GST rate in hundredths of a percent: 18 -> 1800, 0.25 -> 25
    var x = Number(g);
    if (SLABS.indexOf(x) < 0) throw err(g + "% isn't a GST rate.");
    return Math.round(x * 100);
  }

  function isInt(x) { return typeof x === "number" && isFinite(x) && Math.floor(x) === x; }

  /* bill: {lines: [{item, qty, price, mrp, gst, disc}], bill_disc, round, buyer: {gstin, name, address}}
   * returns the worked-out bill, or throws an Error with a message for the cashier */
  function compute(bill) {
    var src = bill.lines || [];
    if (!src.length) throw err("Add at least one item.");
    var lines = [];
    var i, l;
    for (i = 0; i < src.length; i++) {
      l = src[i];
      var name = String(l.item || "");
      var qm = milli(l.qty);
      if (qm <= 0) throw err("Quantity of " + name + " must be more than zero.");
      if (qm > 100000000) throw err("Quantity of " + name + " is too large.");
      var price = l.price, mrp = l.mrp || 0, disc = l.disc || 0;
      if (!isInt(price) || price <= 0) throw err("Price of " + name + " must be more than zero.");
      if (price > 10000000) throw err("Price of " + name + " is too large (over Rs 1,00,000 a unit).");
      if (!isInt(mrp) || mrp < 0) throw err("MRP of " + name + " isn't right.");
      if (mrp && price > mrp) throw err(name + " can't be sold above its MRP.");
      if (!isInt(disc) || disc < 0) throw err("Discount on " + name + " isn't right.");
      var gh = rateH(l.gst);
      var gross = roundDiv(price * qm, 1000);
      if (disc >= gross) throw err("Discount on " + name + " is more than its amount.");
      lines.push({ item: name, qm: qm, price: price, mrp: mrp, gst: Number(l.gst), gh: gh, gross: gross,
                   line_disc: disc, share: 0 });
    }
    // bill discount: shared over the lines by their amounts (largest remainder, ties to the earlier line)
    var bd = bill.bill_disc || 0;
    if (!isInt(bd) || bd < 0) throw err("Bill discount isn't right.");
    var W = 0;
    for (i = 0; i < lines.length; i++) W += lines[i].gross - lines[i].line_disc;
    if (bd >= W) throw err("Bill discount is more than the bill.");
    if (bd) {
      var given = 0, rems = [], BW = BigInt(W), BD = BigInt(bd);  // BigInt: bd * w can pass 2^53
      for (i = 0; i < lines.length; i++) {
        var w = BigInt(lines[i].gross - lines[i].line_disc);
        var s = (BD * w) / BW;
        lines[i].share = Number(s); given += Number(s);
        rems.push({ i: i, r: BD * w - s * BW });
      }
      rems.sort(function (a, b) { return a.r === b.r ? a.i - b.i : (b.r > a.r ? 1 : -1); });
      for (i = 0; given < bd; i++, given++) lines[rems[i].i].share += 1;
    }
    for (i = 0; i < lines.length; i++) {
      l = lines[i];
      l.disc = l.line_disc + l.share;
      l.amount = l.gross - l.disc; // what the customer pays for this line, GST included
      if (l.amount <= 0) throw err("Amount of " + l.item + " must be more than zero after discount.");
    }
    // tax per GST rate, in the order the rates first appear
    var order = [], groups = {};
    for (i = 0; i < lines.length; i++) {
      l = lines[i];
      if (!groups[l.gh]) { groups[l.gh] = { gst: l.gst, gh: l.gh, amount: 0, idx: [] }; order.push(l.gh); }
      groups[l.gh].amount += l.amount;
      groups[l.gh].idx.push(i);
    }
    var taxes = [], total = 0, taxableTotal = 0, cgstTotal = 0;
    for (var k = 0; k < order.length; k++) {
      var g = groups[order[k]];
      var half = g.gh ? roundDiv(g.amount * g.gh, 2 * (10000 + g.gh)) : 0;
      var taxable = g.amount - 2 * half;
      var sum = 0;
      for (var j = 0; j < g.idx.length; j++) {
        var ln = lines[g.idx[j]];
        ln.taxable = j === g.idx.length - 1 ? taxable - sum : roundDiv(ln.amount * 10000, 10000 + g.gh);
        sum += ln.taxable;
      }
      taxes.push({ gst: g.gst, amount: g.amount, taxable: taxable, cgst: half, sgst: half });
      total += g.amount; taxableTotal += taxable; cgstTotal += half;
    }
    var amount = bill.round ? roundDiv(total, 100) * 100 : total;
    var mrpTotal = 0, savings = 0, gstLines = 0;
    for (i = 0; i < lines.length; i++) {
      l = lines[i];
      if (l.gh) gstLines++;
      if (l.mrp) {
        var m = roundDiv(l.mrp * l.qm, 1000);
        mrpTotal += m;
        if (m > l.amount) savings += m - l.amount;
      }
    }
    var buyer = bill.buyer || {};
    var registered = !!(buyer.gstin && String(buyer.gstin).trim());
    if (registered && !gstinOk(buyer.gstin)) throw err("The customer's GSTIN " + buyer.gstin + " isn't right. Check it.");
    if (!registered && amount >= B2C_DETAILS_FROM && (!String(buyer.name || "").trim() || !String(buyer.address || "").trim()))
      throw err("For a bill of Rs 50,000 or more, enter the customer's name and address (GST rule 46).");
    var title = gstLines === lines.length ? "Tax Invoice" : gstLines === 0 ? "Bill of Supply"
      : registered ? "Tax Invoice" : "Invoice-cum-Bill of Supply";
    return {
      lines: lines.map(function (x) {
        return { item: x.item, qm: x.qm, price: x.price, mrp: x.mrp, gst: x.gst, gross: x.gross,
                 disc: x.disc, amount: x.amount, taxable: x.taxable };
      }),
      taxes: taxes, total: total, round_off: amount - total, amount: amount,
      taxable: taxableTotal, cgst: cgstTotal, sgst: cgstTotal, mrp_total: mrpTotal, savings: savings,
      b2b: registered, title: title
    };
  }

  var GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  function gstinOk(g) {
    g = String(g || "").trim().toUpperCase();
    if (!/^[0-9]{2}[A-Z0-9]{10}[0-9A-Z]Z[0-9A-Z]$/.test(g)) return false;
    var sum = 0;
    for (var i = 0; i < 14; i++) {
      var v = GSTIN_CHARS.indexOf(g[i]) * (i % 2 ? 2 : 1);
      sum += Math.floor(v / 36) + (v % 36);
    }
    return GSTIN_CHARS[(36 - (sum % 36)) % 36] === g[14];
  }

  // Rs display: 123456 paise -> "1,234.56"
  function rs(p) {
    var neg = p < 0; p = Math.abs(p);
    var r = Math.floor(p / 100), f = String(p % 100).padStart(2, "0");
    var s = String(r), out = s.slice(-3), rest = s.slice(0, -3);
    while (rest.length > 2) { out = rest.slice(-2) + "," + out; rest = rest.slice(0, -2); }
    if (rest) out = rest + "," + out;
    return (neg ? "-" : "") + out + "." + f;
  }
  function qty(qm) { var s = (qm / 1000).toFixed(3).replace(/\.?0+$/, ""); return s; }
  function paise(text) { // "12.5" -> 1250
    var s = String(text == null ? "" : text).trim();
    if (s === "") return 0;
    if (!/^\d+(\.\d{0,2})?$/.test(s)) return NaN;
    var p = s.split(".");
    return parseInt(p[0] || "0", 10) * 100 + parseInt((p[1] || "").padEnd(2, "0"), 10);
  }

  var ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
              "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"];
  var TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"];
  function two(n) { return n < 20 ? ONES[n] : TENS[Math.floor(n / 10)] + (n % 10 ? " " + ONES[n % 10] : ""); }
  function three(n) { return (n >= 100 ? ONES[Math.floor(n / 100)] + " Hundred" + (n % 100 ? " " : "") : "") + two(n % 100); }
  function words(p) { // Indian system: "Rupees One Lakh Twenty Thousand ... and Fifty Paise Only"
    var r = Math.floor(p / 100), ps = p % 100, parts = [];
    var cr = Math.floor(r / 10000000); r %= 10000000;
    var lk = Math.floor(r / 100000); r %= 100000;
    var th = Math.floor(r / 1000); r %= 1000;
    if (cr) parts.push(three(cr) + " Crore");
    if (lk) parts.push(two(lk) + " Lakh");
    if (th) parts.push(two(th) + " Thousand");
    if (r) parts.push(three(r));
    var s = "Rupees " + (parts.length ? parts.join(" ") : "Zero");
    if (ps) s += " and " + two(ps) + " Paise";
    return s + " Only";
  }

  return { SLABS: SLABS, compute: compute, gstinOk: gstinOk, milli: milli, roundDiv: roundDiv, rs: rs, qty: qty,
           paise: paise, words: words, B2C_DETAILS_FROM: B2C_DETAILS_FROM };
});
