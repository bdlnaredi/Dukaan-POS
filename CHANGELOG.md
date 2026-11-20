# Changelog

## 0.5.0
- **Windows app** (Dukaan-Setup.exe, built by GitHub Actions and published under Releases → Windows app):
  - It can run Dukaan on the PC itself (handy when TallyPrime is on the same PC), with the owner's password chosen in the app, a taskbar icon, Start with Windows, and the address and QR code for phones on the same Wi-Fi.
  - Or it can open an existing Dukaan server as a billing counter in its own window.
  - The installer allows Dukaan through Windows Firewall and adds Start menu shortcuts. Uninstalling keeps your data.
- `main.serve()` starts Dukaan without blocking, so the Windows app can run it.

## 0.4.0
- **Online store link (StoreKart)**, off until the owner turns it on in Settings → Online store link:
  - Online orders put their items aside here as soon as they're placed, so live stock and the counters show it straight away.
  - Completed, paid orders become bills in their own series (W/2627/1 …). They go into Tally like any counter bill and earn loyalty points.
  - A cancelled order gives its stock back, or cancels its bill (Tally's own Cancel) if it was already made.
  - StoreKart owner/admin can sign in here as the owner, and managers and cashiers as cashiers ("Online store login" on the sign-in screen). Dukaan cashiers can sign in to StoreKart with their name and PIN.
  - Turned off, or with the online store unreachable, Dukaan works on its own as before.
- tests/test_link.py: Dukaan, StoreKart and the fake Tally together.

## 0.3.0
- Live stock: Tally's stock less Dukaan bills that Tally's count doesn't have yet, refreshed on the counters every minute; reorder level per item, "Low stock" list, warning (or block, in Settings) when selling past stock
- Batches and expiry: enter batches with batch no., MFD, EXP and quantity; sales take the batch that expires first and skip expired ones; returns go back to their batch; "Expiring" list; warning (or block) for items with only expired batches; print labels for a batch; optional batch names to Tally
- Offers: buy X get Y free, % or Rs off items or item groups (from a quantity), and % or Rs off bills from an amount; dates; applied by themselves at the counter and shown on the bill; offer report
- Loyalty points by mobile number: earn per Rs 100, use at payment (from a minimum, up to a % of the bill), taken back on returns and cancels, owner can add or take away; customer list with history
- Day end: opening cash, cash in/out, closing count by notes and coins, short/extra worked out, closing slip to print, closings in Reports
- Android app (Dukaan.apk on the repo's "android" release): phone scanner, printing, RawBT, WhatsApp, downloads, updates itself

## 0.2.0
- Product labels: name, net quantity, MFD, EXP, MRP, selling price, savings and barcode (EAN-13 or Code 128), with optional "MRP incl. of all taxes", "Packed by" and FSSAI. Label printer rolls or A4 sheets. "Make one" gives an item a shop barcode (EAN-13 starting 21) that scans at the counter
- Cancel bills that are already in Tally: Tally's own Cancel on the bill's vouchers (kept, marked cancelled, amounts zero; nothing deleted), owner only, with a reason, until GSTR-1 for that month is due

## 0.1.1
- Fixed: typing in the owner password, quantity, price and cash received boxes did nothing (only Enter got through)

## 0.1.0 (first version)
- Counter billing on phone, tablet or PC: search, barcode scanning (USB/Bluetooth scanner or camera), favourites, loose items by weight or amount, hold bills
- GST tax invoices (and bills of supply) with HSN, rate-wise CGST/SGST, place of supply; GST rates and HSN from Tally; selling above MRP blocked
- Cash with change, UPI QR with the amount, card, split payments, credit to Tally customers with part paid now
- Print on 80 mm, 58 mm or A4; RawBT for Bluetooth printers on Android; WhatsApp with a bill link
- Works offline: bills numbered per counter on the device and sent when the server is back
- Each bill goes into Tally as a sales voucher (plus contra or receipt where needed); returns as credit notes; never changes or deletes anything in Tally; never sends a bill twice
- Owner: cashiers with PINs and permissions, counters, items (price, MRP, barcodes), Tally setup, reports (payment modes, GST by rate, HSN summary, documents issued, CSV)
- Nightly database backup
