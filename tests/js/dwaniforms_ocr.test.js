// What Samjhao suggests from a photographed paper: the kind of paper, its last date, the amount.
// Pure text parsing (the OCR engine itself is exercised in a real browser, see dwaniforms/README.md). Run: node dwaniforms_ocr.test.js
const path = require("path");
const O = require(path.join(__dirname, "..", "..", "dwaniforms", "static", "ocr", "dwani-ocr.js"));
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const TODAY = new Date(Date.UTC(2026, 9, 13));      // 13 Oct 2026
function expect(name, text, want) {
  const got = O.read(text, TODAY);
  for (const k of Object.keys(want)) ok(got[k] === want[k], `${name}: ${k} = ${JSON.stringify(got[k])} (want ${JSON.stringify(want[k])})`);
}

expect("English electricity bill", `BSES Rajdhani Power Limited
ELECTRICITY BILL
CA No: 151234567   Bill Date: 05/10/2026
Units Consumed (kWh): 412
Energy Charges 2,345.00
Amount Payable: Rs. 2,890.00
Due Date: 20/10/2026
Pay by due date to avoid late payment surcharge`, { paper: "Electricity bill", due_date: "2026-10-20", amount: "2890" });

expect("Hindi electricity bill, Devanagari digits", `उत्तर प्रदेश पावर कॉर्पोरेशन लिमिटेड
बिजली बिल
बिल दिनांक: ०५-१०-२०२६
कुल देय राशि: ₹ १,८५०
भुगतान की अंतिम तिथि: २५-१०-२०२६`, { paper: "Electricity bill", due_date: "2026-10-25", amount: "1850" });

expect("e-challan", `e-Challan
Challan No: DL123456789   Date: 01-Oct-2026
Vehicle No: DL3CAB1234
Violation: Riding without helmet
Fine Amount: 1000
Pay at echallan.parivahan.gov.in`, { paper: "Traffic challan", due_date: "2026-10-01", amount: "1000" });

expect("court summons", `IN THE COURT OF METROPOLITAN MAGISTRATE, SAKET COURTS, NEW DELHI
SUMMONS TO ACCUSED
Case No. 4512/2026
You are hereby summoned to appear before this court on 12th November 2026 at 10:00 AM.`,
  { paper: "Court summons or legal notice", due_date: "2026-11-12", amount: null });

expect("KYC SMS", `Dear customer your SBI account will be blocked today. Update your PAN/KYC immediately click http://sbi-kyc.xyz`,
  { paper: "Bank KYC or account block message", due_date: null, amount: null });

expect("income tax notice", `INCOME TAX DEPARTMENT
Document Identification Number (DIN): ITBA/AST/S/143(1)/2026-27/1012345678(1)
Assessment Year: 2025-26
Notice under section 143(1)(a)
Tax payable: 12,450
Please reply by 30/10/2026 on incometax.gov.in`, { paper: "Income tax notice", due_date: "2026-10-30", amount: "12450" });

expect("Hindi house tax (gṛhkar)", `नगर निगम लखनऊ
गृहकर बिल वर्ष 2026-27
देय राशि ₹ 3,240
अंतिम तिथि 31/12/2026`, { paper: "Property tax notice", due_date: "2026-12-31", amount: "3240" });

expect("water bill, dotted date", `Delhi Jal Board
Water Bill   K No. 1234567890
Amount Payable 640
Due Date 28.10.2026`, { paper: "Water bill", due_date: "2026-10-28", amount: "640" });

expect("loan overdue, 'by' a date", `Your loan account XXXX1234: EMI of Rs 5,600 is overdue since 05/09/2026.
Please pay the overdue amount of Rs. 11,200 by 20/10/2026 to avoid charges.`,
  { paper: "Bank loan or EMI notice", due_date: "2026-10-20", amount: "11200" });

expect("Hindi 'तक' after the date", `बैंक ऋण सूचना
आपकी किस्त बकाया है। कृपया 20/10/2026 तक जमा करें।`, { paper: "Bank loan or EMI notice", due_date: "2026-10-20" });

// --- when to say nothing: a wrong guess is worse than a question
expect("unreadable photo", `~~ .. ,, || 7 ; ;`, { paper: null, due_date: null, amount: null });
expect("two old dates and no label", `Receipt 05/03/2026 copy 07/03/2026`, { due_date: null });
expect("a bill and a due date both printed, no label: no guess", `Bill 05/10/2026 next 20/10/2026`, { due_date: null });
ok(O.findPaper("premises remitted") === null, "short keywords match whole words only ('emi' is not in 'premises')");
ok(O.findDueDate("Due Date: 31/02/2026", TODAY) === null, "an impossible date is not a date");
ok(O.findAmount("Amount Payable: Rs. 1,23,456.40") === "123456", "Indian digit grouping, paise rounded");
ok(O.findAmount("New Delhi 110001\nConsumer No 4455667788\nAmount Payable 640") === "640",
   "the number after the amount label, not a PIN code or consumer number elsewhere");
