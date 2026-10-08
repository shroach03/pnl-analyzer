// Gmail search pagination: a backlog larger than one page clears in one run, within a time budget.
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { blob, load, makeDrive, makeGmail } = require("./fake_gas.js");

const ACCOUNTANT = "closeout@example-cpa.test";
const PASS = "mx.google.com; dmarc=pass (p=REJECT) header.from=example-cpa.test";
const PROPS = () => ({ INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: ACCOUNTANT });
const email = (i) => ({ id: `msg${String(i).padStart(13, "0")}`, from: `Example CPA <${ACCOUNTANT}>`, auth: PASS,
  attachments: [blob(`doc${i}.pdf`, Buffer.from(`%PDF ${i}`))] });

function backlog(n) {
  const drive = makeDrive();
  const gmail = makeGmail(Array.from({ length: n }, (_, i) => [email(i)]));
  const ctx = load(drive, PROPS(), { Gmail: gmail.Gmail });
  const pdfs = () => drive.myDrive.sub("PnLAnalyze").sub("Inbox").files.filter((f) => f.name.endsWith(".pdf")).length;
  return { drive, gmail, ctx, pdfs };
}

test("a backlog of 250 threads clears in one run", () => {
  const b = backlog(250);
  b.ctx.processInbox();
  assert.equal(b.pdfs(), 250);
  assert.equal(b.gmail.threads.filter((t) => t.labels.includes("pnl/ingested")).length, 250);
  assert.equal(b.gmail.queries.filter((q) => /has:attachment/.test(q)).length, 3);   // pages of 100, 100, 50
  b.ctx.processInbox();                                                               // and nothing is saved twice
  assert.equal(b.pdfs(), 250);
});

test("a search stops at the page cap and says so", () => {
  const b = backlog(25);
  b.ctx.CONFIG.SEARCH_PAGE_SIZE = 10;
  b.ctx.CONFIG.MAX_SEARCH_PAGES = 2;
  b.ctx.processInbox();
  assert.equal(b.pdfs(), 20);
  assert.ok(b.ctx.logs.some((l) => /WARNING: search stopped after 2 pages \(20 threads\)/.test(l)));
  b.ctx.processInbox();                                 // the next run continues where this one stopped
  assert.equal(b.pdfs(), 25);
});

test("past the time budget, the rest is left for the next run and nothing is lost", () => {
  const b = backlog(30);
  b.ctx.CONFIG.RUN_BUDGET_MS = -1;                      // over budget immediately: one thread, then stop
  b.ctx.processInbox();
  assert.equal(b.pdfs(), 1);
  assert.ok(b.ctx.logs.some((l) => /WARNING: time budget reached after 1 thread\(s\); \d+ left for the next run/.test(l)));
  assert.equal(b.gmail.threads.filter((t) => t.labels.length).length, 1);   // only handled threads are labeled
  b.ctx.CONFIG.RUN_BUDGET_MS = 60000;
  b.ctx.processInbox();
  assert.equal(b.pdfs(), 30);
});
