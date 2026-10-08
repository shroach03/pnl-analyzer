// The processed-message memory: a JSON file in the project folder (no longer one 9 KB Script Property).
//   node --test tests/apps_script/*.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { blob, load, makeDrive, makeGmail } = require("./fake_gas.js");

const ACCOUNTANT = "closeout@example-cpa.test";
const PASS = "mx.google.com; dmarc=pass (p=REJECT) header.from=example-cpa.test";
const FILE = "_intake_seen_message_ids.json";
const OLD_PROP = "PNL_PROCESSED_MSG_IDS";
const baseProps = () => ({ INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze", ALLOWED_SENDERS: ACCOUNTANT });

const email = (i) => ({ id: `msg${String(i).padStart(13, "0")}`, from: `Example CPA <${ACCOUNTANT}>`, auth: PASS,
  attachments: [blob(`doc${i}.pdf`, Buffer.from(`%PDF ${i}`))] });
const ids = (n, prefix = "old") => Array.from({ length: n }, (_, i) => `${prefix}${String(i).padStart(13, "0")}`);

function setup(messages, props = baseProps()) {
  const drive = makeDrive();
  const gmail = makeGmail(messages.map((m) => [m]));
  const ctx = load(drive, props, { Gmail: gmail.Gmail });
  const root = () => drive.myDrive.sub("PnLAnalyze");
  const memory = () => JSON.parse(root().getFilesByName(FILE).next().bytes.toString()).ids;
  const inboxFiles = () => root().sub("Inbox").files.map((f) => `${f.name}:${f.bytes.toString().length}`);
  return { drive, gmail, ctx, props, root, memory, inboxFiles };
}

test("2,000+ IDs save to the file and reload without error", () => {
  const t = setup([email(1)]);
  const root = t.drive.myDrive.createFolder("PnLAnalyze");
  root.createFile(FILE, JSON.stringify({ ids: ids(2500) }), "text/plain");
  t.ctx.processInbox();
  const back = t.memory();
  assert.equal(back.length, 2501);
  assert.equal(back[2500], "msg0000000000001");
  assert.equal(JSON.stringify(back.slice(0, 2500)), JSON.stringify(ids(2500)));
  assert.ok(t.root().getFilesByName(FILE).next().bytes.length > 9 * 1024, "well past the old 9 KB property limit");
});

test("the file keeps the most recent 10,000 IDs", () => {
  const t = setup([]);
  const root = t.drive.myDrive.createFolder("PnLAnalyze");
  t.ctx.saveSeenIds_({ order: ids(12000) }, root);
  const back = Array.from(t.ctx.loadSeenIds_(root).order);
  assert.equal(back.length, 10000);
  assert.equal(back[0], ids(12000)[2000]);
});

test("running intake twice on the same mail saves nothing new the second time", () => {
  const t = setup([email(1), email(2)]);
  t.ctx.processInbox();
  const first = t.inboxFiles();
  assert.equal(first.filter((f) => f.includes(".pdf")).length, 2);
  const manifest = t.root().sub("Inbox").getFilesByName("_manifest.csv").next().bytes.toString();
  t.ctx.processInbox();
  assert.deepEqual(t.inboxFiles(), first);
  assert.equal(t.root().sub("Inbox").getFilesByName("_manifest.csv").next().bytes.toString(), manifest);
  assert.deepEqual(t.memory(), ["msg0000000000001", "msg0000000000002"]);
});

test("the migration copies the old property into the file and deletes the property", () => {
  const props = { ...baseProps(), [OLD_PROP]: JSON.stringify(ids(1500)) };
  const t = setup([email(1)], props);
  t.ctx.migrateSeenIdsToFile();
  assert.ok(!(OLD_PROP in props), "the old property no longer exists");
  assert.equal(t.ctx.PropertiesService.getScriptProperties().getProperty(OLD_PROP), null);
  assert.deepEqual(t.memory(), ids(1500));
  assert.ok(t.ctx.logs.some((l) => /Moved 1500 processed-message ID\(s\) .* and deleted the property/.test(l)));
  t.ctx.migrateSeenIdsToFile();                       // a second run is a no-op
  assert.deepEqual(t.memory(), ids(1500));
});

test("processInbox migrates by itself, merges with an existing file, and honours the migrated IDs", () => {
  const already = email(7);
  const props = { ...baseProps(), [OLD_PROP]: JSON.stringify([already.id, "older00000001"]) };
  const t = setup([already, email(8)], props);
  t.drive.myDrive.createFolder("PnLAnalyze").createFile(FILE, JSON.stringify({ ids: ["fromfile00001"] }), "text/plain");
  t.ctx.processInbox();
  assert.ok(!(OLD_PROP in props));
  assert.deepEqual(t.memory(), ["fromfile00001", already.id, "older00000001", "msg0000000000008"]);
  const pdfs = t.inboxFiles().filter((f) => f.includes(".pdf"));
  assert.deepEqual(pdfs.map((f) => f.split(":")[0]), ["20260924_00000008_doc8.pdf"]);   // msg 7 was remembered
});

test("IDs are saved before any thread is labeled", () => {
  const t = setup([email(1)]);
  const order = [];
  const root = t.drive.myDrive.createFolder("PnLAnalyze");
  const create = root.createFile.bind(root);
  root.createFile = (...a) => { if (a[0] === FILE) order.push("save"); return create(...a); };
  t.gmail.threads[0].onModify = () => order.push("label");
  t.ctx.processInbox();
  assert.deepEqual(order, ["save", "label"]);
});

test("a failed save logs the error, stops the run, and labels nothing", () => {
  const t = setup([email(1), email(2)]);
  const root = t.drive.myDrive.createFolder("PnLAnalyze");
  root.createFile = (name, ...rest) => {
    if (name === FILE) throw new Error("Drive quota exceeded");
    return Object.getPrototypeOf(root).createFile.call(root, name, ...rest);
  };
  assert.throws(() => t.ctx.processInbox(), /Saving _intake_seen_message_ids\.json failed: Drive quota exceeded/);
  assert.ok(t.ctx.logs.some((l) => /ERROR: could not save .* Intake stopped before labeling any thread/.test(l)));
  assert.deepEqual(t.gmail.threads.flatMap((th) => th.labels), []);
  assert.ok(!t.ctx.logs.some((l) => /^Run done/.test(l)));
});

test("an unreadable ID file stops intake before anything is saved", () => {
  const t = setup([email(1)]);
  t.drive.myDrive.createFolder("PnLAnalyze").createFile(FILE, "{not json", "text/plain");
  assert.throws(() => t.ctx.processInbox(), /_intake_seen_message_ids\.json is unreadable/);
  assert.deepEqual(t.inboxFiles().filter((f) => f.includes(".pdf")), []);
});

test("a migration that cannot save keeps the old property", () => {
  const props = { ...baseProps(), [OLD_PROP]: JSON.stringify(ids(3)) };
  const t = setup([], props);
  const root = t.drive.myDrive.createFolder("PnLAnalyze");
  root.createFile = () => { throw new Error("no write access"); };
  assert.throws(() => t.ctx.migrateSeenIdsToFile(), /failed: no write access/);
  assert.equal(props[OLD_PROP], JSON.stringify(ids(3)));
});
