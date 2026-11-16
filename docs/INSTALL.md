# Setting up Dukaan

## 1. Get Tally ready

Do this on the PC that runs TallyPrime.

**Let Dukaan talk to Tally**
1. In TallyPrime press **F1 (Help) › Settings › Connectivity › Client/Server configuration**.
2. Set **TallyPrime acts as** to **Both**, **Enable ODBC** to **Yes**, and **Port** to **9000**. Restart TallyPrime.
3. Allow port 9000 through Windows Firewall: Windows Security › Firewall › Advanced settings › Inbound Rules › New Rule › Port › TCP 9000 › Allow.
4. Give the Tally PC a fixed IP address in your router (a "DHCP reservation"), so its address doesn't change.
5. Keep your company open in Tally while the shop is billing. Bills made while Tally is closed wait in Dukaan and go in when it's open again.

If the server and the Tally PC are in different places, install Tailscale on both and use the Tally PC's Tailscale IP.

**A voucher type for counter bills**

Gateway of Tally › Create › Voucher Type:
- Name: **Counter Sales**. Select type of voucher: **Sales**.
- Method of voucher numbering: **Manual**. Prevent duplicates: **Yes**.

Dukaan numbers its own bills (A/2627/1, A/2627/2, …) and Tally keeps those numbers. Make **Counter Returns** the same way under **Credit Note** if you want returns kept apart.

**Ledgers** (most shops already have these):
- **Cash** (Cash-in-Hand).
- The **bank account your UPI money goes into**, and the one your **card machine** pays into (Bank Accounts).
- **Sales ledgers** (Sales Accounts). One per GST rate ("Sales GST 5%", "Sales GST 18%", "Sales Exempt") makes Tally's reports clearest, but one is enough.
- **Output CGST** and **Output SGST** for each rate (Duties & Taxes, type of duty GST, tax type Central Tax / State Tax). For example "Output CGST 2.5%" and "Output SGST 2.5%" for 5% items.
- **Round Off** (Indirect Expenses).
- Customers you give credit to (Sundry Debtors), with their GSTIN where they have one.

**Items**
- Every stock item needs its **HSN code** and **GST rate**, on the item or on its stock group. Dukaan won't sell an item without them.
- For loose items, set the unit (e.g. Kg) to **3 decimal places**.
- A barcode can go in as an **alias** of the item in Tally, or you can add barcodes in Dukaan later.

## 2. Install the server

**Unraid:** Apps › search **dukaan** › Install. Set the **Owner password** and click Apply.

**Any Docker host:** use [docker-compose.yml](../docker-compose.yml). Change `APP_PASSWORD`, then run `docker compose up -d`.

Open `http://SERVER-IP:2510` and sign in as the **Owner** with that password.

## 3. Connect to Tally

1. Open the **Tally** page.
2. Type the Tally PC's address (e.g. `192.168.1.20`) and tap **Connect**. Dukaan finds the open company and copies your items, ledgers, voucher types and godowns. It does this again every 30 minutes, or when you tap **Copy items & ledgers now**.
3. Under **Where bills go in Tally**, Dukaan has guessed the voucher types and ledgers from their names. Check each one, especially the CGST and SGST ledgers for every GST rate.
4. Tick **I've checked these** and **Save**. Nothing goes to Tally until you do.

## 4. Settings

- **Shop details on the bill:** name, address, GSTIN, phone, FSSAI. Left empty, they come from your company in Tally.
- **Printing:** 80 mm, 58 mm or A4, and the line at the bottom of the bill.
- **UPI ID:** the payment QR shows the bill amount.
- **Bill number prefix** (optional). It can't change once you've made bills, because GST needs one series a year.
- **HSN digits:** 6 if your turnover (across your PAN) is above ₹5 crore, otherwise 4. Items with shorter codes are flagged.
- **Counters:** one per billing device, each with its own series letter (A, B, …).
- **Cashiers:** name, PIN, and whether they can give discounts, take returns or give credit.
- **Public address:** if you reach Dukaan from outside through Cloudflare Tunnel (e.g. `https://dukaan.yourdomain.com`), put it here so WhatsApp bills carry a link to the full invoice.

## 5. Items

On the **Items** page:
- **Can't sell** lists items missing a GST rate or HSN code. Fix them in Tally, then copy from Tally again.
- Tap an item to set its **selling price** and **MRP** (when Tally's price list doesn't have them), **barcodes** (scan them in), **loose** (asks for the weight each time) and **favourite** (a quick button on the billing screen).

## 6. The counter devices

- **Phone or tablet:** open the address in Chrome, then menu › **Add to Home screen**. Sign in, then pick the counter.
- **Counter PC:** open it in Chrome or Edge. A USB barcode scanner just works: scan anywhere on the billing screen.
- **Camera scanning and installing as an app need https.** Reach Dukaan through Cloudflare Tunnel or Tailscale (`tailscale serve`) rather than a plain `http://192.168…` address.
- **Thermal printer on a PC:** install its driver and make it the default printer. In Chrome's print window, pick the printer, set margins to None, and tick "Print headers and footers" off once; Chrome remembers.
- **Bluetooth printer on Android:** install **RawBT** from the Play Store and pair the printer in it. Then use the **RawBT** button on the bill.

Keyboard on the PC: **F2** search, **F8** pay, **F4** customer, **F3** hold, **Enter** in the empty search box opens payment.

## The Android app

1. On the phone, open **github.com/bdlnaredi/Dukaan-POS/releases/tag/android**, download **Dukaan.apk**, and tap it to install. Allow "install unknown apps" for the browser when Android asks.
2. Open Dukaan, enter the server address (the https one, e.g. `https://dukaan.yourdomain.com`), and sign in.
3. The camera button uses Google's scanner, so there's no camera permission to give. Print uses Android's print screen; Bluetooth printers still go through RawBT.
4. When a new version is built, the app asks to update itself.

**One-time, so the app can update itself:** the app must be signed with the same key every time. In the Dukaan-POS repo on GitHub, open Settings › Secrets and variables › Actions and add `DUKAAN_KEYSTORE_B64` and `DUKAAN_KEYSTORE_PASSWORD`. (To use Bahi's key instead, copy its two values and also add `DUKAAN_KEY_ALIAS` = `bahi`.) Keep a copy of the key file somewhere safe: without it, future versions can't update the installed app. Then run the "Android app" workflow again. Without them the app still builds, but each new version has to be uninstalled and installed fresh.

## Stock, batches and offers

- **Reorder levels:** Items › an item › Reorder level. Items at or below it show under **Low stock**.
- **Batches:** Items › an item › Add a batch, when stock comes in (batch no., quantity, MFD, EXP). **Expiring** lists batches past their date or within the days set in Settings. Use **Labels** on a batch to print its labels.
- **Batch names in Tally:** only if your items keep batches in Tally with the same names. Tick it on the Tally page.
- **Offers:** the Offers page. They apply by themselves at the counter.
- **Loyalty points:** turn them on in Settings › Loyalty points. The cashier enters the customer's mobile number under Customer, and points show at payment. Points used are a discount on the bill; ask your CA how they want loyalty treated.

## 7. Every day

- **Morning:** each counter asks for the opening cash. You can turn this off in Settings.
- **Closing:** menu › **Day end / cash** › **Close the day**. Count the notes and coins, then print the slip.

- **Reports › Today** at closing: cash in the drawer, UPI, card and credit, and GST by rate.
- **Tally** page: bills **refused** by Tally show the reason (for example a ledger renamed in Tally). Fix it, then **try all again**. Bills **marked for you** need a look before they go.
- B2B bills to customers with a GSTIN: if e-invoicing applies to you, generate the e-invoice in Tally once the bill is there.

## 8. Trial run

For the first week or two, bill in Dukaan alongside your current method. Each evening compare Dukaan's report with the vouchers in Tally (Day Book), and have your CA look at a few bills and their GST. Switch over once they match.

## Questions

**Dukaan was offline. What happens to the bills?** They were numbered and printed on the device. The top bar shows "N to send"; they go to the server as soon as it can be reached, and on to Tally from there. Don't clear the browser's data on a device with bills waiting.

**Two devices on one counter?** Don't. Each counter's numbers are kept on its device. If you move a counter to another device, bills still waiting on the old one are marked for the owner.

**Can I change a bill?** No. A bill in the customer's hand stays as it is. The owner can **cancel** it (Bills › the bill › Cancel bill, with a reason) and bill again. If it's already in Tally it's cancelled there too, with Tally's own Cancel. That is allowed until GSTR-1 for its month is due (the 10th of the next month); after that, take the goods back as a return (credit note).

**Labels.** Labels page (owner): pick an item, set MFD and EXP, check the price and MRP, and add it to the print list with the number of copies. Choose your label size and print. On a label printer, first set its paper size (in Windows' printer settings) to the label size, and set margins to None in the print window. Scan the first label before printing many.
