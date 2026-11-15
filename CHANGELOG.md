# Changelog

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
