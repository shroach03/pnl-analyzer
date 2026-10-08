// The 7-day catch-up search: link-only mail is kept, but only from allowed senders, and a burst can't flood Drive.
//   node --test tests/apps_script/*.test.js
//
// The fake Gmail returns every thread for every query, i.e. the worst case where the search filter let
// everything through. So these tests exercise the script's own backstop as well as the query it builds.
const test = require("node:test");
const assert = require("node:assert/strict");
const { blob, load, makeDrive, makeGmail } = require("./fake_gas.js");

const ACCOUNTANT = "closeout@example-cpa.test";
const PASS = "mx.google.com; dmarc=pass (p=REJECT) header.from=example-cpa.test";
const PROPS = { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: ACCOUNTANT };

const stranger = (i, over = {}) => ({ id: `junk${String(i).padStart(12, "0")}`, from: `Promo <deals${i}@spam.test>`,
  subject: `You won ${i}`, body: `Click https://spam.test/${i} now`, attachments: [], ...over });
const linkOnly = (i) => ({ id: `link${String(i).padStart(12, "0")}`, from: `Example CPA <${ACCOUNTANT}>`, auth: PASS,
  subject: `Statement ${i}`, body: `Your statement: https://portal.example-cpa.test/share/${i}`, attachments: [] });

/** Run processInbox() `runs` times; return every file the script created in Drive, by folder (except its own ID memory). */
function run(messages, runs = 1) {
  const drive = makeDrive();
  const gmail = makeGmail(messages.map((m) => [m]));
  const ctx = load(drive, { ...PROPS }, { Gmail: gmail.Gmail });
  for (let i = 0; i < runs; i++) ctx.processInbox();
  const files = {};
  const walk = (folder, prefix) => {
    for (const f of folder.files) if (f.name !== "_intake_seen_message_ids.json") files[`${prefix}${f.name}`] = f.bytes.toString();
    for (const sub of folder.folders) walk(sub, `${prefix}${sub.name}/`);
  };
  walk(drive.myDrive, "");
  return { files, logs: ctx.logs, queries: gmail.queries };
}

test("the catch-up search only looks at allowed senders", () => {
  const { queries } = run([]);
  assert.equal(queries.length, 2);
  assert.match(queries[0], /has:attachment/);                                    // the main search
  assert.equal(queries[1], `to:intake@example.test newer_than:7d from:(${ACCOUNTANT}) -label:pnl-refused`);
  const two = makeGmail([]);
  load(makeDrive(), { ...PROPS, ALLOWED_SENDERS: `${ACCOUNTANT}, backup@example-cpa.test` }, { Gmail: two.Gmail }).processInbox();
  assert.match(two.queries[1], /from:\((closeout|backup)@example-cpa\.test OR (closeout|backup)@example-cpa\.test\) -label:pnl-refused$/);
});

test("a plain-text email from a stranger creates nothing", () => {
  const { files, logs } = run([stranger(1)]);
  assert.deepEqual(files, {});
  assert.ok(logs.some((l) => /Ignored junk\d+ \(no attachments\): sender deals1@spam\.test is not on ALLOWED_SENDERS/.test(l)));
});

test("a link-only email from the accountant still creates one note", () => {
  const { files } = run([linkOnly(1)]);
  const notes = Object.keys(files).filter((n) => /^PnLAnalyze\/Inbox\/LINK_ONLY_/.test(n));
  assert.equal(notes.length, 1);
  assert.match(files[notes[0]], /- https:\/\/portal\.example-cpa\.test\/share\/1/);
  assert.match(files["PnLAnalyze/Inbox/_manifest.csv"], /,link_only\n$/);
});

test("a burst of 50 junk emails creates no files", () => {
  const junk = Array.from({ length: 50 }, (_, i) => stranger(i, i % 2 ? { auth: PASS, from: `"${ACCOUNTANT}" <x${i}@spam.test>` } : {}));
  const { files } = run(junk, 2);
  assert.deepEqual(files, {});
});

test("junk mixed in with real mail does not crowd it out", () => {
  const junk = Array.from({ length: 50 }, (_, i) => stranger(i));
  const { files } = run([...junk, linkOnly(1)]);
  assert.equal(Object.keys(files).filter((n) => n.includes("LINK_ONLY_")).length, 1);
});

test("one run writes at most 10 notes, warns, and leaves the rest for the next run", () => {
  const burst = Array.from({ length: 15 }, (_, i) => linkOnly(i));
  const once = run(burst, 1);
  assert.equal(Object.keys(once.files).filter((n) => n.includes("LINK_ONLY_")).length, 10);
  assert.ok(once.logs.some((l) => /WARNING: note cap of 10 reached; 5 message\(s\) left for the next run/.test(l)));
  const twice = run(burst, 2);
  assert.equal(Object.keys(twice.files).filter((n) => n.includes("LINK_ONLY_")).length, 15);   // nothing lost
});

test("the cap never holds back PDFs", () => {
  const burst = Array.from({ length: 12 }, (_, i) => linkOnly(i));
  const pdf = { ...linkOnly(99), id: "pdf000000000099", attachments: [blob("Riverside_GL.pdf", Buffer.from("%PDF gl"))] };
  const { files } = run([...burst, pdf]);
  assert.ok("PnLAnalyze/Inbox/20260924_00000099_Riverside_GL.pdf" in files);
});

test("a stranger's email with an attachment still leaves a rejected-sender note (fix 1 is unchanged)", () => {
  const { files } = run([stranger(1, { attachments: [blob("invoice.pdf", Buffer.from("%PDF x"))] })]);
  assert.deepEqual(Object.keys(files).filter((n) => !n.endsWith("_manifest.csv")),
    ["PnLAnalyze/Rejected/REJECTED_SENDERS_20260924_000000.txt"]);
});
