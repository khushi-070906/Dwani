// Document reader: what is suggested from a photographed Aadhaar card, ration card or khasra copy. Pure text parsing.
// Run: node dwaniforms_docs.test.js
const path = require("path");
const D = require(path.join(__dirname, "..", "..", "dwaniforms", "static", "ocr", "dwani-docs.js"));
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) process.exitCode = 1; };
const TODAY = new Date(Date.UTC(2026, 9, 14));
const AAD = "234567890124", AADG = "2345 6789 0124";
const eq = (name, got, want) => ok(JSON.stringify(got) === JSON.stringify(want), name + ": " + JSON.stringify(got) + " (want " + JSON.stringify(want) + ")");

// the check digit is the same as the server's
ok(D.verhoeff(AAD), "a real Aadhaar number passes the check");
ok(!D.verhoeff(AAD.slice(0, 11) + String((+AAD[11] + 1) % 10)), "one wrong digit fails it");

const front = `Government of India
Asha Verma
जन्म तिथि / DOB: 14/03/1990
महिला / Female
${AADG}
VID : 9123 4567 8901 2345`;
let r = D.read(front, TODAY);
eq("aadhaar front kind", r.kind, "aadhaar");
eq("number", r.values.aadhaar, AAD);
eq("date of birth", r.values.dob, "1990-03-14");
eq("gender", r.values.gender, "Female");
eq("name from the line above the date of birth", r.values.full_name, "Asha Verma");
ok(!("mobile" in r.values) && !("pincode" in r.values), "nothing is invented for what is not on this side");

const back = `Address: S/O: Ramesh Verma, 12 Gandhi Road, Rampur,
Lucknow, Uttar Pradesh - 226001
${AADG}
Mobile: 98765 43210   PIN Code: 226001`;
r = D.read(back, TODAY);
eq("aadhaar back: number", r.values.aadhaar, AAD);
eq("father from S/O", r.values.father_name, "Ramesh Verma");
eq("PIN code by its label", r.values.pincode, "226001");
eq("mobile by its label", r.values.mobile, "9876543210");

eq("male", D.read(`Ravi Kumar\nDOB: 01/01/1985\nMALE\n${AADG}`, TODAY).values.gender, "Male");
eq("Hindi male", D.read(`आधार\nजन्म तिथि: 01/01/1985\nपुरुष\n${AADG}`, TODAY).values.gender, "Male");
eq("Devanagari digits", D.read(`आधार\nजन्म तिथि: १४/०३/१९९०\n${AADG.replace(/\d/g, (d) => "०१२३४५६७८९"[d])}`, TODAY).values.dob, "1990-03-14");

// a misread digit, a virtual ID, a card number: never an Aadhaar number
eq("wrong check digit is dropped", D.read("Aadhaar " + AAD.slice(0, 8) + "0000", TODAY).kind, null);
eq("a 16-digit virtual ID is not an Aadhaar number", D.findAadhaar("VID : 9123 4567 8901 2345"), null);
eq("a number starting with 0 or 1", D.findAadhaar("0234 5678 9012"), null);
eq("a date of birth in the future is dropped", D.findDob("DOB: 14/03/2090", TODAY), null);
eq("only a year of birth: no date", D.findDob("Year of Birth: 1990", TODAY), null);
eq("an impossible date is dropped", D.findDob("DOB: 31/02/1990", TODAY), null);
eq("a heading is not a name", D.findName("Government of India\nUnique Identification Authority\nDOB: 14/03/1990"), null);
eq("one word is not a name", D.findName("Asha\nDOB: 14/03/1990"), null);

// ration card
r = D.read(`खाद्य एवं रसद विभाग उत्तर प्रदेश\nराशन कार्ड\nराशन कार्ड संख्या: 0912345678901\nमुखिया: आशा`, TODAY);
eq("ration card (Hindi labels): kind", r.kind, "ration");
eq("ration card number", r.values.ration_card, "0912345678901");
eq("also offered for the ration look-up", r.values.card_no, "0912345678901");
eq("a card number cut by spaces is not guessed", D.findRation("Ration Card No: UP12 3456 7890\nNFSA"), null);
eq("ration card with a dash", D.read("Ration Card No. 0912-3456-7890\nNFSA", TODAY).values.ration_card, "091234567890");
eq("a ration word without a number", D.read("Ration card\nFood department", TODAY).kind, null);

// khasra
eq("khasra number", D.read("खतौनी\nखसरा संख्या 145/2\nग्राम रामपुर", TODAY).values.khasra, "145/2");
eq("khasra in English", D.read("Khasra No. 87 / 3  Village Rampur", TODAY).values.khasra, "87/3");
eq("khasra kind", D.read("Khasra No: 12", TODAY).kind, "khasra");

// nothing
eq("a bill is none of these", D.read("Electricity bill, amount payable Rs 2,890, due date 20/10/2026", TODAY).kind, null);
eq("empty", D.read("", TODAY), { kind: null, values: {} });
ok(!/\(\?<[=!]/.test(require("fs").readFileSync(path.join(__dirname, "..", "..", "dwaniforms", "static", "ocr", "dwani-docs.js"), "utf8").replace(/^\s*(\/\/|\*|\/\*).*$/gm, "")),
   "no look-behind in any pattern (older iPhones refuse the whole script)");
