/* Dukaan offers: which offer applies to which bill line, and how much it takes off.
 * Each line gets the one offer that saves the most; a line with a discount typed in by hand gets none.
 * Then the best bill offer (from a minimum bill) applies to what is left. Amounts are whole paise. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Offers = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function live(o, day) { return o.active !== false && (!o.start || o.start <= day) && (!o.end || o.end >= day); }

  function milli(q) {
    var s = String(q == null ? "" : q).trim(), p = s.split(".");
    if (!/^\d*(\.\d{0,3})?$/.test(s) || s === "") return 0;
    return (p[0] ? parseInt(p[0], 10) : 0) * 1000 + parseInt((p[1] || "").padEnd(3, "0") || "0", 10);
  }
  function rdiv(a, b) { return Math.floor((2 * a + b) / (2 * b)); }

  function covers(o, line, items) {
    var d = o.data || {};
    var it = items[line.item] || {};
    return (d.items || []).indexOf(line.item) >= 0 || (it.group && (d.groups || []).indexOf(it.group) >= 0);
  }

  /* how much an offer takes off one line (paise), 0 when it doesn't apply */
  function lineOff(o, line) {
    var d = o.data || {}, qm = milli(line.qty), gross = rdiv(line.price * qm, 1000), off = 0;
    if (o.kind === "bxgy") {
      var units = Math.floor(qm / 1000), sets = Math.floor(units / (d.buy + d.free));
      off = sets * d.free * line.price;
    } else if (o.kind === "off") {
      if (d.min_qty && qm < d.min_qty * 1000) return 0;
      off = d.pct > 0 ? rdiv(gross * Math.round(d.pct * 100), 10000) : rdiv(d.amt * qm, 1000);
    }
    return Math.max(0, Math.min(off, gross - 1));      // something is always paid for the line
  }

  /* lines: the cart lines ({item, qty, price, disc}); returns {lines: [{disc, offer}], bill: {id, name, amount} | null,
   * used: [{id, name, amount}]} */
  function apply(lines, offers, day, items) {
    offers = (offers || []).filter(function (o) { return live(o, day); });
    var out = [], used = {}, rest = 0;
    lines.forEach(function (l) {
      var best = null, bestOff = 0;
      if (!l.disc && !l.noOffer) {
        offers.forEach(function (o) {
          if (o.kind === "bill" || !covers(o, l, items || {})) return;
          var x = lineOff(o, l);
          if (x > bestOff) { bestOff = x; best = o; }
        });
      }
      out.push({ disc: bestOff, offer: best ? { id: best.id, name: best.name } : null });
      if (best) {
        var u = used[best.id] || (used[best.id] = { id: best.id, name: best.name, amount: 0 });
        u.amount += bestOff;
      }
      rest += rdiv(l.price * milli(l.qty), 1000) - (l.disc || 0) - bestOff;
    });
    var bill = null;
    offers.forEach(function (o) {
      if (o.kind !== "bill") return;
      var d = o.data || {};
      if (rest < (d.min_bill || 0) || rest <= 0) return;
      var x = d.pct > 0 ? rdiv(rest * Math.round(d.pct * 100), 10000) : d.amt;
      if (d.max && x > d.max) x = d.max;
      x = Math.min(x, rest - 1);
      if (x > 0 && (!bill || x > bill.amount)) bill = { id: o.id, name: o.name, amount: x };
    });
    var list = Object.keys(used).map(function (k) { return used[k]; });
    if (bill) list.push(bill);
    return { lines: out, bill: bill, used: list };
  }

  return { apply: apply, lineOff: lineOff, live: live };
});
