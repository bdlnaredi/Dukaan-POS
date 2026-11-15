/* Barcodes as SVG, for labels: EAN-13 for 13-digit codes (what shop scanners expect on products),
 * Code 128 for anything else. No outside library. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Barcode = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------------------------------------------------------------- EAN-13
  var L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"];
  var R = L.map(function (s) { return s.replace(/./g, function (c) { return c === "0" ? "1" : "0"; }); });
  var G = R.map(function (s) { return s.split("").reverse().join(""); });
  var PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"];

  function eanCheck(d12) {
    var s = 0;
    for (var i = 0; i < 12; i++) s += Number(d12[i]) * (i % 2 ? 3 : 1);
    return String((10 - (s % 10)) % 10);
  }
  function isEan13(code) { return /^\d{13}$/.test(code) && eanCheck(code.slice(0, 12)) === code[12]; }

  function ean13(code) {
    var p = PARITY[Number(code[0])];
    var bits = "101";
    for (var i = 1; i <= 6; i++) bits += (p[i - 1] === "L" ? L : G)[Number(code[i])];
    bits += "01010";
    for (i = 7; i <= 12; i++) bits += R[Number(code[i])];
    bits += "101";
    return bits;
  }

  // ---------------------------------------------------------------- Code 128 (B for text, C for long digit runs)
  var C128 = ["212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213", "221312", "231212",
    "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131", "311222",
    "321122", "321221", "312212", "322112", "322211", "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113",
    "132311", "211313", "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331", "231131",
    "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114", "122411", "142112", "142211", "241211", "221114",
    "413111", "241112", "134111", "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141", "114131", "311141", "411131",
    "211412", "211214", "211232", "2331112"];
  var START_B = 104, START_C = 105, TO_C = 99, TO_B = 100, STOP = 106;

  function code128(text) {
    for (var k = 0; k < text.length; k++) {
      var cc = text.charCodeAt(k);
      if (cc < 32 || cc > 126) throw new Error("A barcode can only have plain letters, digits and symbols.");
    }
    var vals = [], i = 0, set = null;
    var digitsAt = function (j) { var n = 0; while (j + n < text.length && /\d/.test(text[j + n])) n++; return n; };
    while (i < text.length) {
      var run = digitsAt(i);
      var useC = run >= 4 && (set === "C" || run >= (i === 0 || i + run === text.length ? 4 : 6));
      if (useC) {
        if (run % 2) {               // odd run: one digit in B first
          if (set !== "B") { vals.push(set === null ? START_B : TO_B); set = "B"; }
          vals.push(text.charCodeAt(i) - 32); i++; run--;
        }
        if (set !== "C") { vals.push(set === null ? START_C : TO_C); set = "C"; }
        for (var e = i + run; i < e; i += 2) vals.push(Number(text.substr(i, 2)));
      } else {
        if (set !== "B") { vals.push(set === null ? START_B : TO_B); set = "B"; }
        vals.push(text.charCodeAt(i) - 32); i++;
      }
    }
    var sum = vals[0];
    for (i = 1; i < vals.length; i++) sum += vals[i] * i;
    vals.push(sum % 103, STOP);
    var bits = "";
    vals.forEach(function (v) {
      var w = C128[v];
      for (var j = 0; j < w.length; j++) bits += (j % 2 ? "0" : "1").repeat(Number(w[j]));
    });
    return bits;
  }

  /* SVG of the barcode with its digits underneath. opts: {height (in modules), text (true/false)} */
  function svg(code, opts) {
    opts = opts || {};
    code = String(code || "").trim();
    var ean = isEan13(code);
    var bits = ean ? ean13(code) : code128(code);
    var q = ean ? 11 : 10;                       // quiet zones
    var W = bits.length + 2 * q;
    var H = opts.height || 50;
    var th = opts.text === false ? 0 : 11;
    var guard = ean ? 5 : 0;
    var rects = "", x = 0;
    while (x < bits.length) {
      if (bits[x] === "1") {
        var s = x;
        while (bits[x] === "1") x++;
        var tall = ean && (s < 3 || (s >= 45 && s < 50) || s >= 92);
        rects += '<rect x="' + (s + q) + '" y="0" width="' + (x - s) + '" height="' + (H + (tall ? guard : 0)) + '"/>';
      } else x++;
    }
    var txt = "";
    if (th) {
      var y = H + th - 1;
      var st = 'font-family="Arial,Helvetica,sans-serif" font-size="10" text-anchor="middle"';
      if (ean) {
        txt = '<text x="' + (q - 5) + '" y="' + y + '" ' + st + '>' + code[0] + "</text>" +
          '<text x="' + (q + 3 + 21) + '" y="' + y + '" ' + st + ' letter-spacing="1.6">' + code.slice(1, 7) + "</text>" +
          '<text x="' + (q + 50 + 21) + '" y="' + y + '" ' + st + ' letter-spacing="1.6">' + code.slice(7) + "</text>";
      } else {
        txt = '<text x="' + W / 2 + '" y="' + y + '" ' + st + '>' + code.replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</text>";
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + W + " " + (H + th + (th ? 0 : guard)) +
      '" preserveAspectRatio="none" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><g fill="#000">' +
      rects + "</g>" + txt + "</svg>";
  }

  return { svg: svg, isEan13: isEan13, eanCheck: eanCheck, ean13: ean13, code128: code128 };
});
