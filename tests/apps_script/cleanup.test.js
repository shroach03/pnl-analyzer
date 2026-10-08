// cleanupInbox(): what an agent-written _processed_*.json may and may not get trashed.
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { hex, load, makeDrive } = require("./fake_gas.js");

const OLD = Buffer.from("%PDF original Hilltop P&L");
const NEW = Buffer.from("%PDF corrected Hilltop P&L");

/** A project tree with one Inbox file, a superseded original (backup in place), and files elsewhere. */
function project() {
  const drive = makeDrive();
  const root = drive.myDrive.createFolder("PnLAnalyze");
  const inbox = root.createFolder("Inbox");
  const month = root.createFolder("Stores").createFolder("S02").createFolder("2026-08");
  const f = {
    drive, root, inbox, month,
    manifest: inbox.createFile("_manifest.csv", "received_ts\n", "text/csv"),
    inboxPdf: inbox.createFile("20260911_c7feb29a_Hilltop GL Aug 2026.pdf", Buffer.from("%PDF gl"), "application/pdf"),
    oldCanonical: month.createFile("S02_FR_2026-08.pdf", OLD, "application/pdf"),
    newCanonical: month.createFile("S02_FR_2026-08.pdf", NEW, "application/pdf"),     // the copy the agent made
    gl: month.createFile("S02_GL_2026-08.pdf", Buffer.from("%PDF gl"), "application/pdf"),
    backup: month.createFolder("superseded").createFile("S02_FR_2026-08_superseded_20260915.pdf", OLD, "application/pdf"),
    report: root.createFolder("Reports").createFile("2026-08_portfolio_report.md", "# report", "text/markdown"),
    quarantined: root.createFolder("Quarantine").createFile("scan.pdf", Buffer.from("%PDF scan"), "application/pdf"),
  };
  // The person approved the correction now holding S02_FR_2026-08.pdf (approveCorrections() writes this).
  f.props = { ROOT_FOLDER: "PnLAnalyze",
    PNL_APPROVED_CORRECTIONS: JSON.stringify({ [hex("sha256", NEW)]: { file: "S02_FR_2026-08.pdf", approved_at: "2026-09-15" } }) };
  f.ctx = load(drive, f.props);
  return f;
}

/** Write one cleanup list into the Inbox, run cleanupInbox(), return the error file's text ('' if none). */
function clean(f, entries) {
  f.inbox.createFile("_processed_20260915_0900.json", JSON.stringify({ written_by: "agent sweep", trash: entries }), "application/json");
  f.ctx.cleanupInbox();
  const err = f.inbox.files.find((x) => x.name.startsWith("_cleanup_error_"));
  return err ? err.bytes.toString() : "";
}
const entry = (file, reason) => ({ drive_file_id: file.id, title: file.name, reason });

test("a file directly inside Inbox/ is trashed, and the list is retired", () => {
  const f = project();
  assert.equal(clean(f, [entry(f.inboxPdf, "filed")]), "");
  assert.equal(f.inboxPdf.trashed, true);
  assert.equal(f.inbox.files.find((x) => x.name.startsWith("_processed_")).trashed, true);
});

for (const [where, pick] of [["Stores/", (f) => f.gl], ["Reports/", (f) => f.report], ["Quarantine/", (f) => f.quarantined]]) {
  test(`a file in ${where} is refused and logged`, () => {
    const f = project();
    const target = pick(f);
    const err = clean(f, [entry(target, "filed")]);
    assert.equal(target.trashed, false);
    assert.match(err, new RegExp(`${target.id} \\(${target.name.replace(/[.]/g, "\\.")}, reason filed\\): refused, is not directly inside Inbox/`));
  });
}

test("Inbox control files and Inbox subfolders are refused", () => {
  const f = project();
  const sub = f.inbox.createFolder("nested");
  const nested = sub.createFile("x.pdf", Buffer.from("%PDF x"), "application/pdf");
  const err = clean(f, [entry(f.manifest, "filed"), entry(sub, "filed"), entry(nested, "filed")]);
  assert.equal(f.manifest.trashed, false);
  assert.equal(nested.trashed, false);
  assert.match(err, /_manifest\.csv, reason filed\): refused, is an Inbox control file/);
  assert.match(err, /nested, reason filed\): refused, is a folder/);
  assert.match(err, /x\.pdf, reason filed\): refused, is not directly inside Inbox\//);
});

test("a file outside the project, or in two folders, is refused", () => {
  const f = project();
  const elsewhere = f.drive.myDrive.createFolder("Personal").createFile("taxes.pdf", Buffer.from("%PDF"), "application/pdf");
  f.inboxPdf.parents.push(f.month);      // also filed in Stores/: trashing it would remove it there too
  const err = clean(f, [entry(elsewhere, "filed"), entry(f.inboxPdf, "filed")]);
  assert.equal(elsewhere.trashed, false);
  assert.equal(f.inboxPdf.trashed, false);
  assert.match(err, /taxes\.pdf, reason filed\): refused, is not directly inside Inbox\//);
  assert.match(err, /refused, is in 2 folders/);
});

// ---------------------------------------------------------------- the one exception
test("a replaced original whose identical backup exists in superseded/ is trashed", () => {
  const f = project();
  assert.equal(clean(f, [entry(f.oldCanonical, "superseded_old_canonical")]), "");
  assert.equal(f.oldCanonical.trashed, true);
  assert.equal(f.newCanonical.trashed, false);
  assert.equal(f.backup.trashed, false);
});

test("a replaced original whose backup is missing is refused", () => {
  const f = project();
  f.month.sub("superseded").files = [];
  const err = clean(f, [entry(f.oldCanonical, "superseded_old_canonical")]);
  assert.equal(f.oldCanonical.trashed, false);
  assert.match(err, /refused, superseded_old_canonical, but no identical backup \(S02_FR_2026-08_superseded_YYYYMMDD\.pdf\) is in 2026-08\/superseded\//);
});

test("a backup with the right name but different bytes does not count", () => {
  const f = project();
  f.backup.setContent(NEW);
  assert.match(clean(f, [entry(f.oldCanonical, "superseded_old_canonical")]), /no identical backup/);
  assert.equal(f.oldCanonical.trashed, false);
});

test("the exception covers nothing but canonical documents in this project's Stores/SXX/YYYY-MM/", () => {
  const f = project();
  // a backup in the same folder as the target doesn't make a Reports/ file or a superseded backup trashable
  const err = clean(f, [entry(f.report, "superseded_old_canonical"), entry(f.backup, "superseded_old_canonical")]);
  assert.equal(f.report.trashed, false);
  assert.equal(f.backup.trashed, false);
  assert.match(err, /2026-08_portfolio_report\.md, reason superseded_old_canonical\): refused, superseded_old_canonical, but .* is not a canonical/);
  assert.match(err, /S02_FR_2026-08_superseded_20260915\.pdf, reason superseded_old_canonical\): refused, .* is not a canonical/);

  const g = project();
  const other = g.drive.myDrive.createFolder("Copy").createFolder("Stores").createFolder("S02").createFolder("2026-08");
  const stray = other.createFile("S02_FR_2026-08.pdf", OLD, "application/pdf");
  other.createFolder("superseded").createFile("S02_FR_2026-08_superseded_20260915.pdf", OLD, "application/pdf");
  assert.match(clean(g, [entry(stray, "superseded_old_canonical")]), /not in this project's Stores\/S02\/2026-08\//);
  assert.equal(stray.trashed, false);
});

test("allowed entries still go through when others in the same list are refused", () => {
  const f = project();
  const err = clean(f, [entry(f.inboxPdf, "filed"), entry(f.gl, "filed"), entry(f.oldCanonical, "superseded_old_canonical")]);
  assert.equal(f.inboxPdf.trashed, true);
  assert.equal(f.oldCanonical.trashed, true);
  assert.equal(f.gl.trashed, false);
  assert.match(err, /completed with 1 error\(s\)/);
});

// ---------------------------------------------------------------- the script checks its own evidence
test("an Inbox PDF listed as filed, quarantined or pending with no copy elsewhere is refused", () => {
  for (const reason of ["filed", "quarantined", "pending_approval"]) {
    const f = project();
    const orphan = f.inbox.createFile("20260912_deadbeef_only_copy.pdf", Buffer.from("%PDF only copy"), "application/pdf");
    const err = clean(f, [entry(orphan, reason)]);
    assert.equal(orphan.trashed, false, reason);
    assert.ok(err.includes(`refused, ${reason}, but no identical copy (same SHA-256) exists elsewhere in the project folder`), err);
  }
});

test("a filed Inbox PDF whose copy is in Quarantine/ or pending/ counts as saved", () => {
  const f = project();
  const scan = f.inbox.createFile("20260912_f3f71406_scan.pdf", Buffer.from("%PDF scan"), "application/pdf");  // = Quarantine/scan.pdf
  const corr = f.inbox.createFile("20260912_3bdb791c_revised.pdf", NEW, "application/pdf");                 // = the new canonical
  assert.equal(clean(f, [entry(scan, "quarantined"), entry(corr, "pending_approval")]), "");
  assert.equal(scan.trashed, true);
  assert.equal(corr.trashed, true);
});

test("a copy that is itself in the Inbox, or already trashed, does not count", () => {
  const f = project();
  const a = f.inbox.createFile("20260912_aaaaaaaa_x.pdf", Buffer.from("%PDF twin"), "application/pdf");
  f.inbox.createFile("20260912_bbbbbbbb_x.pdf", Buffer.from("%PDF twin"), "application/pdf");
  const trashedCopy = f.month.createFile("S02_BR_2026-08.pdf", Buffer.from("%PDF twin"), "application/pdf");
  trashedCopy.trashed = true;
  assert.match(clean(f, [entry(a, "filed")]), /no identical copy/);
  assert.equal(a.trashed, false);
});

test("a duplicate is trashed only when an earlier manifest row has the same hash", () => {
  const f = project();
  const first = f.inbox.createFile("20260908_c1e15fbd_GL.pdf", Buffer.from("%PDF resent"), "application/pdf");
  const resend = f.inbox.createFile("20260910_6d743faa_GL.pdf", Buffer.from("%PDF resent"), "application/pdf");
  const stray = f.inbox.createFile("20260911_99999999_GL.pdf", Buffer.from("%PDF never seen"), "application/pdf");
  const sha = (b) => hex("sha256", Buffer.from(b));
  f.manifest.setContent([
    "received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,sha256,note",
    `t1,m1,a,s,GL.pdf,${first.id},${sha("%PDF resent")},`,
    `t2,m2,a,s,GL.pdf,${resend.id},${sha("%PDF resent")},`,
    `t3,m3,a,s,GL.pdf,${stray.id},${sha("%PDF never seen")},`,
  ].join("\n") + "\n");
  const err = clean(f, [entry(resend, "duplicate"), entry(first, "duplicate"), entry(stray, "duplicate")]);
  assert.equal(resend.trashed, true);                       // row 1 came first with the same hash
  assert.equal(first.trashed, false);                       // nothing earlier: it is the original
  assert.equal(stray.trashed, false);
  assert.match(err, /GL\.pdf, reason duplicate\): refused, duplicate, but no earlier _manifest\.csv row has its SHA-256/);
});

test("an Inbox PDF listed with a reason that never applies to PDFs is refused", () => {
  const f = project();
  assert.match(clean(f, [entry(f.inboxPdf, "link_only_logged")]), /refused, reason link_only_logged cannot certify an Inbox PDF/);
  assert.equal(f.inboxPdf.trashed, false);
});

test("a link-only note needs no copy: it holds no document", () => {
  const f = project();
  const note = f.inbox.createFile("LINK_ONLY_20260913_dae9c274.txt", "links", "text/plain");
  assert.equal(clean(f, [entry(note, "link_only_logged")]), "");
  assert.equal(note.trashed, true);
});

test("a replaced original is kept unless its replacement is an approved correction", () => {
  const f = project();
  f.props.PNL_APPROVED_CORRECTIONS = JSON.stringify({});   // nobody ran approveCorrections()
  const err = clean(f, [entry(f.oldCanonical, "superseded_old_canonical")]);
  assert.equal(f.oldCanonical.trashed, false);
  assert.match(err, /no approved correction holds S02_FR_2026-08\.pdf \(approvals are recorded only by approveCorrections\(\)\)/);
});
