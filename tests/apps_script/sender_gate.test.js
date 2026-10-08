// The intake sender gate: only the accountant's authenticated mail reaches Inbox/.
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { blob, load, makeDrive, makeGmail } = require("./fake_gas.js");

const ACCOUNTANT = "closeout@example-cpa.test";
const PASS = "mx.google.com; dkim=pass header.i=@example-cpa.test header.s=s1; " +
  "spf=pass (google.com: domain of closeout@example-cpa.test designates 192.0.2.1 as permitted sender) " +
  "smtp.mailfrom=closeout@example-cpa.test; dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=example-cpa.test";
const BODY = "Confidential: wire instructions changed, see attached.";
const PDF = blob("Riverside_Aug2026_Financials.pdf", Buffer.from("%PDF pretend P&L"));

/** Run processInbox() once over the given messages; return the Drive tree and the manifest rows. */
function intake(messages, props = {}) {
  const drive = makeDrive();
  const gmail = makeGmail([messages]);
  const all = { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: ACCOUNTANT, ...props };
  const ctx = load(drive, all, { Gmail: gmail.Gmail });
  ctx.processInbox();
  const root = drive.myDrive.sub("PnLAnalyze");
  const inbox = root.sub("Inbox");
  const rejected = root.getFoldersByName("Rejected").hasNext() ? root.sub("Rejected") : null;
  const manifest = ctx.parseCsv_(inbox.getFilesByName("_manifest.csv").next().bytes.toString());
  return { ctx, inbox, rejected, header: manifest[0], rows: manifest.slice(1), gmail };
}

const msg = (over) => ({ id: "18f0aa00c1e15fbd", from: `Example CPA <${ACCOUNTANT}>`, auth: PASS, attachments: [PDF], body: BODY, ...over });
const inboxPdfs = (r) => r.inbox.files.filter((f) => f.name !== "_manifest.csv");

function assertRejected(r, reasonPattern) {
  assert.deepEqual(inboxPdfs(r).map((f) => f.name), [], "nothing may be saved to Inbox/");
  assert.equal(r.rejected.files.length, 1, "one rejected-sender note");
  const note = r.rejected.files[0];
  assert.equal(note.name, "REJECTED_SENDERS_20260924_000000.txt");
  const text = note.bytes.toString();
  assert.match(text, reasonPattern);
  assert.match(text, /Riverside_Aug2026_Financials\.pdf \(not saved\)/);
  assert.ok(!text.includes(BODY), "the email body is never copied");
  assert.equal(r.rows.length, 1, "one manifest row");
  const row = Object.fromEntries(r.header.map((h, i) => [h, r.rows[0][i]]));
  assert.equal(row.original_filename, note.name);
  assert.equal(row.drive_file_id, note.id);
  assert.equal(row.sha256, "");
  assert.match(row.note, /^rejected_sender: /);
  assert.match(row.note, reasonPattern);
}

test("the accountant's authenticated mail is saved to the Inbox as before", () => {
  const r = intake([msg({})]);
  assert.deepEqual(inboxPdfs(r).map((f) => f.name), ["20260924_c1e15fbd_Riverside_Aug2026_Financials.pdf"]);
  assert.equal(r.rejected, null);
  assert.equal(r.rows.length, 1);
  assert.equal(r.rows[0][7], "");
});

test("mail from an unlisted address leaves no Inbox file, one rejected-sender note and one manifest row", () => {
  const r = intake([msg({ from: "Billing <billing@lookalike-cpa.test>", auth: PASS.replace(/example-cpa/g, "lookalike-cpa") })]);
  assertRejected(r, /billing@lookalike-cpa\.test is not on ALLOWED_SENDERS/);
});

test("the accountant's address that fails authentication is rejected the same way", () => {
  const spoofed = "mx.google.com; spf=fail smtp.mailfrom=closeout@example-cpa.test; dmarc=fail (p=NONE) header.from=example-cpa.test";
  assertRejected(intake([msg({ auth: spoofed })]), /closeout@example-cpa\.test is allowed but failed authentication: DMARC fail/);
});

test("the accountant's address with no authentication header at all is rejected", () => {
  assertRejected(intake([msg({ auth: "" })]), /failed authentication: no Authentication-Results header/);
});

test("a display name that looks like the accountant's address does not pass for it", () => {
  const r = intake([msg({ from: `"${ACCOUNTANT}" <closeout@evil.test>` })]);
  assertRejected(r, /closeout@evil\.test is not on ALLOWED_SENDERS/);
});

test("addresses compare without regard to case, on the list or in the header", () => {
  const r = intake([msg({ from: "Example CPA <CloseOut@Example-CPA.test>" })], { ALLOWED_SENDERS: " CLOSEOUT@example-cpa.test ; other@x.test" });
  assert.equal(inboxPdfs(r).length, 1);
  assert.equal(r.rejected, null);
});

test("a rejected message is remembered, so the next run does not re-check or re-note it", () => {
  const drive = makeDrive();
  const props = { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: ACCOUNTANT };
  const gmail = makeGmail([[msg({ from: "x@evil.test" })]]);
  const ctx = load(drive, props, { Gmail: gmail.Gmail });
  ctx.processInbox();
  ctx.processInbox();
  assert.equal(drive.myDrive.sub("PnLAnalyze").sub("Rejected").files.length, 1);
});

test("intake refuses to run without ALLOWED_SENDERS and saves nothing", () => {
  const drive = makeDrive();
  const gmail = makeGmail([[msg({})]]);
  const ctx = load(drive, { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze" }, { Gmail: gmail.Gmail });
  assert.throws(() => ctx.processInbox(), /ALLOWED_SENDERS/);
  assert.equal(drive.myDrive.folders.length, 0);
});

// ---------------------------------------------------------------- the pure pieces
test("bareAddress_ takes the address in the final angle brackets, lowercased", () => {
  const ctx = load(makeDrive());
  assert.equal(ctx.bareAddress_("Pat Doe <Pat@Example.TEST>"), "pat@example.test");
  assert.equal(ctx.bareAddress_("pat@example.test"), "pat@example.test");
  assert.equal(ctx.bareAddress_('"a@b.test" <c@d.test>'), "c@d.test");
  assert.equal(ctx.bareAddress_("no address here"), "");
  assert.equal(ctx.bareAddress_(""), "");
});

test("authFailure_: DMARC decides; otherwise only an aligned DKIM or SPF pass counts; only Gmail's header is trusted", () => {
  const ctx = load(makeDrive());
  const d = "example-cpa.test";
  assert.equal(ctx.authFailure_(PASS, d), "");
  assert.equal(ctx.authFailure_("mx.google.com; dkim=pass header.i=@example-cpa.test; dmarc=quarantine", d), "DMARC quarantine");
  assert.equal(ctx.authFailure_("mx.google.com; dkim=pass header.d=mail.example-cpa.test", d), "");           // aligned subdomain
  assert.equal(ctx.authFailure_("mx.google.com; spf=pass smtp.mailfrom=bounce@example-cpa.test", d), "");
  assert.match(ctx.authFailure_("mx.google.com; dkim=pass header.i=@bulk-mailer.test; spf=pass smtp.mailfrom=x@bulk-mailer.test", d),
    /no SPF\/DKIM pass aligned/);
  assert.match(ctx.authFailure_("mx.google.com; dkim=fail header.i=@example-cpa.test; spf=softfail smtp.mailfrom=x@example-cpa.test", d),
    /no SPF\/DKIM pass aligned/);
  assert.match(ctx.authFailure_("evil.test; dmarc=pass header.from=example-cpa.test", d), /not written by mx\.google\.com/);
  assert.match(ctx.authFailure_("", d), /no Authentication-Results header/);
  assert.equal(ctx.domainsAlign_("notexample-cpa.test", d), false);   // a suffix is not a subdomain
});
