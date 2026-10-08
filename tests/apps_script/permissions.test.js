// The script's Google permissions: an explicit, minimal scope list, and Gmail through the Advanced Gmail service.
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { load, makeDrive, makeGmail } = require("./fake_gas.js");

const DIR = path.join(__dirname, "..", "..", "apps-script");
const manifest = JSON.parse(fs.readFileSync(path.join(DIR, "appsscript.json"), "utf8"));
// Code only, comments stripped: a service named in a comment doesn't make Google ask for its scope.
const code = fs.readFileSync(path.join(DIR, "Code.gs"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

test("appsscript.json lists exactly the chosen scopes, so Google infers nothing", () => {
  assert.deepEqual([...manifest.oauthScopes].sort(), [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/script.scriptapp",
  ]);
  assert.ok(!manifest.oauthScopes.includes("https://mail.google.com/"), "never full mailbox control");
  assert.deepEqual(manifest.dependencies.enabledAdvancedServices, [{ userSymbol: "Gmail", serviceId: "gmail", version: "v1" }]);
});

test("the code uses no service that would need a scope outside that list", () => {
  for (const service of ["GmailApp", "MailApp", "UrlFetchApp", "SpreadsheetApp", "DocumentApp", "CalendarApp",
    "ContactsApp", "FormApp", "SlidesApp", "HtmlService", "Session.getActiveUser", "Session.getEffectiveUser"]) {
    assert.ok(!code.includes(service), `Code.gs uses ${service}`);
  }
  assert.ok(!/\bDrive\.(Files|Drives|Permissions)\b/.test(code), "the Advanced Drive service is not used");
  // the only Gmail calls are reading threads/attachments and managing labels: nothing sends, trashes or deletes
  const calls = [...new Set(code.match(/Gmail\.Users\.[A-Za-z.]+/g))].sort();
  assert.deepEqual(calls, ["Gmail.Users.Labels.create", "Gmail.Users.Labels.list", "Gmail.Users.Messages.Attachments.get",
    "Gmail.Users.Messages.modify",
    "Gmail.Users.Threads.get", "Gmail.Users.Threads.list", "Gmail.Users.Threads.modify"]);
  assert.match(code, /addLabelIds/);
  assert.ok(!/removeLabelIds|\.trash\(|\.delete\(|\.send\(/.test(code));
});

// ---------------------------------------------------------------- the Gmail adapter
const b64 = (x) => Buffer.from(x).toString("base64url");
const part = (mimeType, filename, body, headers = []) => ({ mimeType, filename, headers, body });

function message(parts, headers) {
  return {
    id: "18f0aa00c1e15fbd", internalDate: String(Date.parse("2026-09-08T21:02:00Z")),
    payload: { mimeType: "multipart/mixed", filename: "", headers: headers || [
      { name: "Authentication-Results", value: "mx.google.com; dmarc=pass" },
      { name: "From", value: "Example CPA <closeout@example-cpa.test>" },
      { name: "authentication-results", value: "evil.test; dmarc=pass" },          // a later, forged copy
      { name: "Subject", value: "Riverside close" }], parts },
  };
}

test("messages read like GmailApp: headers, date, bodies", () => {
  const ctx = load(makeDrive());
  const m = ctx.wrapMessage_(message([part("multipart/alternative", "", {}, []),
    part("text/plain", "", { data: b64("plain body") }), part("text/html", "", { data: b64("<p>html</p>") })]));
  assert.equal(m.getId(), "18f0aa00c1e15fbd");
  assert.equal(m.getFrom(), "Example CPA <closeout@example-cpa.test>");
  assert.equal(m.getSubject(), "Riverside close");
  assert.equal(m.getHeader("Authentication-Results"), "mx.google.com; dmarc=pass");   // the first one: Gmail's own
  assert.equal(m.getDate().toISOString(), "2026-09-08T21:02:00.000Z");
  assert.equal(m.getPlainBody(), "plain body");
  assert.equal(m.getBody(), "<p>html</p>");
});

test("attachments: inline images are skipped, inline PDFs and nested parts are kept, small parts decode in place", () => {
  const drive = makeDrive();
  const ctx = load(drive, undefined, { Gmail: { Users: { Messages: { Attachments: {
    get: (user, msgId, attId) => ({ data: b64(`%PDF from ${attId}`) }) } } } } });
  const m = ctx.wrapMessage_(message([
    part("text/plain", "", { data: b64("hi") }),
    part("image/png", "logo.png", { attachmentId: "img1" }, [{ name: "Content-ID", value: "<logo>" }]),
    part("application/pdf", "Statement.pdf", { attachmentId: "pdf1" }, [{ name: "Content-Disposition", value: "inline; filename=Statement.pdf" }]),
    { mimeType: "multipart/mixed", filename: "", parts: [part("application/pdf", "GL.pdf", { data: b64("%PDF small") })] },
    part("image/jpeg", "receipt.jpg", { attachmentId: "jpg1" }, [{ name: "Content-Disposition", value: "attachment" }]),
  ]));
  const atts = m.getAttachments();
  assert.deepEqual(Array.from(atts, (a) => a.getName()), ["Statement.pdf", "GL.pdf", "receipt.jpg"]);
  assert.equal(atts[0].copyBlob().getDataAsString(), "%PDF from pdf1");
  assert.equal(atts[1].copyBlob().getDataAsString(), "%PDF small");
  assert.equal(atts[1].copyBlob().getName(), "GL.pdf");
  assert.equal(atts[0].getContentType(), "application/pdf");
});

test("the processed label is created once and reused; threads get it through Threads.modify", () => {
  const gmail = makeGmail([[{ id: "m1", from: "x@spam.test", attachments: [] }]]);
  const ctx = load(makeDrive(), undefined, { Gmail: gmail.Gmail });
  const a = ctx.getOrCreateLabel_("pnl/ingested");
  const b = ctx.getOrCreateLabel_("pnl/ingested");
  assert.equal(a.id, b.id);
  assert.equal(gmail.labels.length, 1);
  const [thread] = ctx.searchThreads_("to:intake@example.test");
  thread.addLabel(a);
  assert.deepEqual(gmail.threads[0].labels, ["pnl/ingested"]);
  assert.equal(thread.getPermalink(), "https://mail.google.com/mail/u/0/#all/thread0");
  assert.deepEqual(Array.from(ctx.searchThreads_("nothing")).length, 1);   // the fake returns every thread
});

test("an empty search result is handled", () => {
  const ctx = load(makeDrive(), undefined, { Gmail: makeGmail([]).Gmail });
  assert.deepEqual(Array.from(ctx.searchThreads_("to:x")), []);
});
