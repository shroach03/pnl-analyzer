// Runs the real apps-script/Code.gs against a fake in-memory Drive.
//   node --test tests/apps_script/
const test = require("node:test");
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const CODE = fs.readFileSync(path.join(__dirname, "..", "..", "apps-script", "Code.gs"), "utf8");
const hex = (alg, buf) => crypto.createHash(alg).update(buf).digest("hex");

function iter(items) {
  let i = 0;
  return { hasNext: () => i < items.length, next: () => items[i++] };
}

/** A tiny Drive: folders, files with bytes, last-updated stamps, makeCopy. */
function makeDrive() {
  let clock = 0;
  let ids = 0;
  class File {
    constructor(name, content, mime) {
      this.name = name;
      this.mime = mime;
      this.id = `f${++ids}`;
      this.setContent(content);
    }
    setContent(c) { this.bytes = Buffer.isBuffer(c) ? c : Buffer.from(c, "utf8"); this.updated = ++clock; }
    getName() { return this.name; }
    getBlob() {
      const f = this;
      return { getBytes: () => Array.from(f.bytes, (b) => (b > 127 ? b - 256 : b)), getDataAsString: () => f.bytes.toString("utf8") };
    }
    getLastUpdated() { return new Date(this.updated); }
    makeCopy(name, dest) { return dest.createFile(name, this.bytes, this.mime); }
  }
  class Folder {
    constructor(name) { this.name = name; this.files = []; this.folders = []; }
    getName() { return this.name; }
    getFoldersByName(n) { return iter(this.folders.filter((f) => f.name === n)); }
    getFilesByName(n) { return iter(this.files.filter((f) => f.name === n)); }
    getFiles() { return iter(this.files); }
    getFolders() { return iter(this.folders); }
    createFolder(name) { const f = new Folder(name); this.folders.push(f); return f; }
    createFile(name, content, mime) { const f = new File(name, content, mime); this.files.push(f); return f; }
    sub(name) { return this.getFoldersByName(name).next(); }
  }
  return { File, Folder, myDrive: new Folder("My Drive") };
}

function load(drive, props = { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze" }) {
  const logs = [];
  const digest = (alg, bytes) => Array.from(crypto.createHash(alg).update(Buffer.from(bytes.map((b) => b & 255))).digest(), (b) => (b > 127 ? b - 256 : b));
  const ctx = vm.createContext({
    DriveApp: { getRootFolder: () => drive.myDrive },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => props[k] }) },
    Logger: { log: (...a) => logs.push(a.join(" ")) },
    Utilities: { DigestAlgorithm: { MD5: "md5", SHA_256: "sha256" }, computeDigest: digest, formatDate: () => "2026-09-24T00:00:00-05:00" },
    Session: { getScriptTimeZone: () => "UTC" },
    MimeType: { CSV: "text/csv", PLAIN_TEXT: "text/plain" },
  });
  vm.runInContext(CODE, ctx);
  ctx.logs = logs;
  return ctx;
}

// ---------------------------------------------------------------- pure helpers
test("digests match published test vectors", () => {
  const ctx = load(makeDrive());
  const abc = Array.from(Buffer.from("abc"));
  assert.equal(ctx.sha256Hex_(abc), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.equal(ctx.digestHex_("md5", abc), "900150983cd24fb0d6963f7d28e17f72");
});

test("csv parse and serialize round-trip quotes, commas, newlines and CRLF", () => {
  const ctx = load(makeDrive());
  const rows = [["a", "b,c", 'say "hi"', "line1\nline2", ""], ["x", "", "", "", "y"]];
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.parseCsv_(ctx.serializeCsv_(rows)))), rows);
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.parseCsv_("h1,h2\r\nv1,v2\r\n\r\n"))), [["h1", "h2"], ["v1", "v2"]]);
});

test("manifest rows: mapped, already sha256, link-only and unmapped are each handled", () => {
  const ctx = load(makeDrive());
  const sha = "a".repeat(64);
  const rows = [
    ["ts", "msg", "from", "subj", "orig", "id", "md5", "note"],
    ["t1", "m", "f", "s", "a.pdf", "1", "1".repeat(32), ""],
    ["t2", "m", "f", "s", "b.pdf", "2", sha.toUpperCase(), ""],   // written by the new script before migrating
    ["t3", "m", "f", "s", "note.txt", "3", "", "link_only"],
    ["t4", "m", "f", "s", "gone.pdf", "4", "2".repeat(32), ""],
  ];
  const out = ctx.migrateManifestRows_(rows, { ["1".repeat(32)]: "b".repeat(64) });
  assert.deepEqual(JSON.parse(JSON.stringify(out.stats)), { mapped: 1, kept: 1, blank: 1, unmapped: 1 });
  assert.equal(out.rows[0][6], "sha256");
  assert.equal(out.rows[1][6], "b".repeat(64));
  assert.equal(out.rows[2][6], sha.toUpperCase());
  assert.equal(out.rows[3][6], "");
  assert.equal(out.rows[3][7], "link_only");
  assert.equal(out.rows[4][6], "");
  assert.match(out.rows[4][7], /sha256 unavailable/);
  assert.throws(() => ctx.migrateManifestRows_(out.rows, {}), /no md5 column/);
});

// ---------------------------------------------------------------- preview and apply against a fake Drive
const A = Buffer.from("%PDF pretend statement A");
const B = Buffer.from("%PDF pretend ledger B");
const C = Buffer.from("%PDF pretend scan C");
const HEADER = "received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,md5,note";

function fixture(rootName = "PnLAnalyze") {
  const drive = makeDrive();
  const root = drive.myDrive.createFolder(rootName);
  const inbox = root.createFolder("Inbox");
  const store = root.createFolder("Stores").createFolder("S01").createFolder("2026-07");
  store.createFile("S01_FR_2026-07.pdf", A, "application/pdf");
  store.createFolder("superseded").createFile("S01_GL_2026-07_superseded_20260801.pdf", B, "application/pdf");
  root.createFolder("Quarantine").createFile("scan.pdf", C, "application/pdf");
  const manifest = [
    HEADER,
    `t1,m1,"Sender <s@example.test>","Fwd: July, final",a.pdf,id1,${hex("md5", A)},`,
    `t2,m2,"Sender <s@example.test>","Fwd: July, final",a.pdf,id2,${hex("md5", A)},`,    // re-send: same bytes
    `t3,m3,s@example.test,ledger,b.pdf,id3,${hex("md5", B)},`,
    `t4,m4,s@example.test,scan,c.pdf,id4,${hex("md5", C)},`,
    `t5,m5,s@example.test,link,LINK_ONLY_x.txt,id5,,link_only`,
    `t6,m6,s@example.test,old,never-archived.pdf,id6,${"0".repeat(32)},`,
  ].join("\n") + "\n";
  const file = inbox.createFile("_manifest.csv", manifest, "text/csv");
  return { drive, root, inbox, file, original: manifest, ctx: load(drive) };
}

test("preview writes a preview and a map but leaves the live manifest untouched", () => {
  const f = fixture();
  const stamp = f.file.updated;
  f.ctx.previewSha256Migration();
  assert.equal(f.file.bytes.toString(), f.original);
  assert.equal(f.file.updated, stamp);
  const preview = f.root.getFilesByName("_manifest_sha256_preview.csv").next().bytes.toString();
  const rows = f.ctx.parseCsv_(preview);
  assert.equal(rows[0].join(","), "received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,sha256,note");
  assert.equal(rows[1][6], hex("sha256", A));
  assert.equal(rows[2][6], hex("sha256", A));   // the re-send keeps its hash through the filed twin
  assert.equal(rows[3][6], hex("sha256", B));   // found in superseded/
  assert.equal(rows[4][6], hex("sha256", C));   // found in Quarantine/
  assert.equal(rows[5][6], "");
  assert.equal(rows[6][6], "");
  assert.match(rows[6][7], /sha256 unavailable/);
  assert.equal(rows[1][3], "Fwd: July, final"); // embedded comma survived
  const meta = JSON.parse(f.root.getFilesByName("hash_migration_map.json").next().bytes.toString());
  assert.equal(meta.files_hashed, 3);
  assert.deepEqual(meta.pairs, { [hex("md5", A)]: hex("sha256", A), [hex("md5", B)]: hex("sha256", B), [hex("md5", C)]: hex("sha256", C) });
});

test("apply backs the original up byte for byte, then swaps the preview in", () => {
  const f = fixture();
  f.ctx.previewSha256Migration();
  f.ctx.applySha256Migration();
  const backup = f.root.getFilesByName("_manifest_md5_backup.csv").next();
  assert.equal(backup.bytes.toString(), f.original);
  const after = f.ctx.parseCsv_(f.file.bytes.toString());
  assert.equal(after[0].join(","), f.ctx.CONFIG.MANIFEST_HEADER);
  assert.equal(after.length, 7);
  assert.equal(after[3][6], hex("sha256", B));
});

test("apply refuses without a preview, after the manifest changed, and after it already ran", () => {
  const f = fixture();
  assert.throws(() => f.ctx.applySha256Migration(), /previewSha256Migration\(\) first/);
  f.ctx.previewSha256Migration();
  f.file.setContent(f.original + "t7,m7,s,new,d.pdf,id7,,\n");  // intake wrote a row after the preview
  assert.throws(() => f.ctx.applySha256Migration(), /changed after the preview/);
  const g = fixture();
  g.ctx.previewSha256Migration();
  g.ctx.applySha256Migration();
  assert.throws(() => g.ctx.applySha256Migration(), /changed after|already/);
  assert.throws(() => g.ctx.previewSha256Migration(), /no md5 column/);
});

test("an already-migrated manifest is never rehashed or backed up over", () => {
  const f = fixture();
  f.ctx.previewSha256Migration();
  f.ctx.applySha256Migration();
  const migrated = f.file.bytes.toString();
  assert.throws(() => f.ctx.previewSha256Migration(), /no md5 column/);
  assert.equal(f.file.bytes.toString(), migrated);
  assert.equal(f.root.getFilesByName("_manifest_md5_backup.csv").next().bytes.toString(), f.original);
});

test("migration runs with only ROOT_FOLDER set: it never needs INTAKE_ADDRESS", () => {
  const f = fixture();
  const ctx = load(f.drive, { ROOT_FOLDER: "PnLAnalyze" });
  ctx.previewSha256Migration();
  ctx.applySha256Migration();
  assert.equal(f.ctx.parseCsv_(f.file.bytes.toString())[0][6], "sha256");
});

test("a missing ROOT_FOLDER fails with a clear message and creates no folder", () => {
  const f = fixture("MyPnLReviews");   // a custom folder name, so the default cannot find it
  const ctx = load(f.drive, {});   // a fresh project: no Script Properties at all
  const before = f.drive.myDrive.folders.length;
  assert.throws(() => ctx.previewSha256Migration(), /ROOT_FOLDER is not set.*PnLAnalyze|ROOT_FOLDER is not set/);
  assert.throws(() => ctx.applySha256Migration(), /Root folder "PnLAnalyze" not found/);
  assert.equal(f.drive.myDrive.folders.length, before);
  assert.throws(() => load(f.drive, { ROOT_FOLDER: "Wrong" }).previewSha256Migration(), /matches the folder name exactly/);
});
