// Approving a held correction: recorded in Script Properties by a person-run function, mirrored for the sweep.
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { hex, load, makeDrive, makeGmail } = require("./fake_gas.js");

const NEW = Buffer.from("%PDF corrected Hilltop P&L");
const PROP = "PNL_APPROVED_CORRECTIONS";
const MIRROR = "_approved_corrections.json";

function project(extraProps = {}) {
  const drive = makeDrive();
  const root = drive.myDrive.createFolder("PnLAnalyze");
  const inbox = root.createFolder("Inbox");
  const month = root.createFolder("Stores").createFolder("S02").createFolder("2026-08");
  const pending = month.createFolder("pending");
  const approved = month.createFolder("approved");
  const props = { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: "closeout@example-cpa.test",
    ...extraProps };
  const ctx = load(drive, props, { Gmail: makeGmail([]).Gmail });
  const mirror = () => {
    const it = root.getFilesByName(MIRROR);
    return it.hasNext() ? JSON.parse(it.next().bytes.toString()).approved : null;
  };
  const alerts = () => inbox.files.filter((f) => f.name.startsWith("_integrity_alert_"));
  return { drive, root, inbox, month, pending, approved, props, ctx, mirror, alerts };
}

test("approveCorrections records exactly the files a person moved into approved/", () => {
  const p = project();
  p.approved.createFile("S02_FR_2026-08_pending_30dd8914.pdf", NEW, "application/pdf");
  p.pending.createFile("S02_GL_2026-08_pending_11111111.pdf", Buffer.from("%PDF still held"), "application/pdf");
  p.ctx.approveCorrections();
  const record = JSON.parse(p.props[PROP]);
  assert.deepEqual(Object.keys(record), [hex("sha256", NEW)]);
  assert.equal(record[hex("sha256", NEW)].file, "S02_FR_2026-08_pending_30dd8914.pdf");
  assert.deepEqual(Object.keys(p.mirror()), [hex("sha256", NEW)]);
  assert.ok(p.ctx.logs.some((l) => l.startsWith("Approved S02_FR_2026-08_pending_30dd8914.pdf (SHA-256 ")));
  assert.ok(p.ctx.logs.some((l) => /^1 new correction\(s\) approved/.test(l)));
});

test("a file merely placed in approved/ is not approved until a person runs the function", () => {
  const p = project();
  p.approved.createFile("S02_FR_2026-08_pending_30dd8914.pdf", NEW, "application/pdf");
  p.ctx.processInbox();                                  // the scheduled run never approves anything
  assert.equal(p.props[PROP], undefined);
  assert.equal(p.mirror(), null);
});

test("a forged approval in the Drive mirror is removed and reported", () => {
  const recorded = hex("sha256", NEW);
  const p = project({ [PROP]: JSON.stringify({ [recorded]: { file: "a.pdf", approved_at: "2026-09-15" } }) });
  const forged = "f".repeat(64);
  p.root.createFile(MIRROR, JSON.stringify({ approved: { [recorded]: {}, [forged]: { file: "evil.pdf" } } }), "text/plain");
  p.ctx.processInbox();
  assert.deepEqual(Object.keys(p.mirror()), [recorded]);
  assert.equal(p.alerts().length, 1);
  assert.match(p.alerts()[0].bytes.toString(), new RegExp(`INTEGRITY ALERT: .*1 approval\\(s\\) that no person recorded[\\s\\S]*- ${forged}`));
});

test("an honest mirror is left alone and raises no alert", () => {
  const recorded = hex("sha256", NEW);
  const p = project({ [PROP]: JSON.stringify({ [recorded]: { file: "a.pdf", approved_at: "2026-09-15" } }) });
  p.ctx.processInbox();                                  // first run writes the missing mirror
  const file = p.root.getFilesByName(MIRROR).next();
  const stamp = file.updated;
  p.ctx.processInbox();
  assert.equal(file.updated, stamp);
  assert.equal(p.alerts().length, 0);
});

test("revokeApprovals withdraws everything not yet applied", () => {
  const p = project({ [PROP]: JSON.stringify({ ["a".repeat(64)]: { file: "a.pdf", approved_at: "2026-09-15" } }) });
  p.ctx.revokeApprovals();
  assert.equal(p.props[PROP], undefined);
  assert.deepEqual(p.mirror(), {});
});

test("the record keeps the 50 most recent approvals, inside the Script Property size limit", () => {
  const old = Object.fromEntries(Array.from({ length: 60 }, (_, i) =>
    [i.toString(16).padStart(64, "0"), { file: `old${i}.pdf`, approved_at: `2026-01-${String(1 + (i % 28)).padStart(2, "0")}T${String(i).padStart(2, "0")}` }]));
  const p = project({ [PROP]: JSON.stringify(old) });
  p.approved.createFile("S02_FR_2026-08_pending_30dd8914.pdf", NEW, "application/pdf");
  p.ctx.approveCorrections();
  const record = JSON.parse(p.props[PROP]);
  assert.equal(Object.keys(record).length, 50);
  assert.ok(record[hex("sha256", NEW)], "the new approval is kept");
  assert.ok(p.props[PROP].length < 9 * 1024);
});
