// Refused mail: one summary note per run, and a pnl/refused label that keeps it out of every later search.
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { blob, load, makeDrive, makeGmail } = require("./fake_gas.js");

const ACCOUNTANT = "closeout@example-cpa.test";
const PASS = "mx.google.com; dmarc=pass (p=REJECT) header.from=example-cpa.test";
const PROPS = () => ({ INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: ACCOUNTANT });
const spam = (i) => ({ id: `spam${String(i).padStart(12, "0")}`, from: `Prize Dept <win${i}@spam.test>`, subject: `Invoice ${i}`,
  attachments: [blob(`invoice${i}.pdf`, Buffer.from(`%PDF spam ${i}`))] });
const real = (i) => ({ id: `real${String(i).padStart(12, "0")}`, from: `Example CPA <${ACCOUNTANT}>`, auth: PASS,
  attachments: [blob(`GL${i}.pdf`, Buffer.from(`%PDF gl ${i}`))] });

function setup(threads) {
  const drive = makeDrive();
  const gmail = makeGmail(threads);
  const ctx = load(drive, PROPS(), { Gmail: gmail.Gmail });
  const root = () => drive.myDrive.sub("PnLAnalyze");
  const rejected = () => (root().getFoldersByName("Rejected").hasNext() ? root().sub("Rejected").files : []);
  const rows = () => root().sub("Inbox").getFilesByName("_manifest.csv").next().bytes.toString().trim().split("\n").slice(1);
  return { drive, gmail, ctx, root, rejected, rows };
}

test("a burst of 30 spam emails with attachments makes one summary note, not 30", () => {
  const t = setup(Array.from({ length: 30 }, (_, i) => [spam(i)]));
  t.ctx.processInbox();
  assert.deepEqual(t.rejected().map((f) => f.name), ["REJECTED_SENDERS_20260924_000000.txt"]);
  const note = t.rejected()[0].bytes.toString();
  assert.match(note, /^REJECTED_SENDERS: 30 email\(s\) refused/);
  assert.equal((note.match(/^From {5}: /gm) || []).length, 30);
  assert.ok(!note.includes("%PDF spam"), "attachments are never saved");
  assert.equal(t.rows().length, 30);                                      // still one manifest row per email
  assert.ok(t.rows().every((r) => r.includes("REJECTED_SENDERS_20260924_000000.txt") && /rejected_sender: /.test(r)));
});

test("refused messages are labeled and never fetched again", () => {
  const t = setup(Array.from({ length: 30 }, (_, i) => [spam(i)]));
  t.ctx.processInbox();
  assert.ok(t.gmail.threads.every((th) => th.messages[0].labels.includes("pnl/refused")));
  const fetched = t.gmail.queries.length;
  t.ctx.processInbox();
  assert.equal(t.rejected().length, 1);
  assert.equal(t.rows().length, 30);
  assert.ok(t.gmail.queries.slice(fetched).every((q) => q.includes("-label:pnl-refused")));
  // and the search, like Gmail's, returned none of them the second time
  assert.match(t.ctx.logs.at(-1), /^Run done: 0 new message\(s\), 0 PDF\(s\) saved, 0 link-only note\(s\), 0 rejected sender\(s\)/);
});

test("the refused label goes on the spam message only, so the accountant's thread is still found", () => {
  const thread = [real(1), spam(1)];                     // a stranger replied on the accountant's thread
  const t = setup([thread]);
  t.ctx.processInbox();
  const [acct, junk] = t.gmail.threads[0].messages;
  assert.ok(junk.labels.includes("pnl/refused"));
  assert.ok(!acct.labels.includes("pnl/refused"));
  t.gmail.threads[0].labels = [];                        // e.g. the accountant replies again: the thread is unprocessed
  t.gmail.threads[0].messages.push({ ...acct, id: "real000000000002", labels: [] });
  t.ctx.processInbox();
  const inbox = t.root().sub("Inbox").files.map((f) => f.name);
  assert.ok(inbox.includes("20260924_00000002_GL1.pdf"), inbox.join(", "));   // the new accountant message was fetched and saved
});

test("a run with nothing refused writes no note", () => {
  const t = setup([[real(1)]]);
  t.ctx.processInbox();
  assert.deepEqual(t.rejected(), []);
});

test("one note lists at most 200 emails and counts the rest", () => {
  const t = setup(Array.from({ length: 205 }, (_, i) => [spam(i)]));
  t.ctx.processInbox();
  const note = t.rejected()[0].bytes.toString();
  assert.equal((note.match(/^From {5}: /gm) || []).length, 200);
  assert.match(note, /\.\.\. and 5 more \(see _manifest\.csv\)/);
  assert.equal(t.rows().length, 205);
});
