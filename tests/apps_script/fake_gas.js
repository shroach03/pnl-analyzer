// A fake Apps Script runtime (Drive, Gmail, Script Properties) for running the real apps-script/Code.gs under Node.
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
      this.parents = [];
      this.trashed = false;
      this.setContent(content);
    }
    setContent(c) { this.bytes = Buffer.isBuffer(c) ? c : Buffer.from(c, "utf8"); this.updated = ++clock; }
    getId() { return this.id; }
    getName() { return this.name; }
    getMimeType() { return this.mime; }
    getSize() { return this.bytes.length; }
    getParents() { return iter(this.parents); }
    isTrashed() { return this.trashed; }
    setTrashed(t) { this.trashed = t; }
    getBlob() {
      const f = this;
      return { getBytes: () => Array.from(f.bytes, (b) => (b > 127 ? b - 256 : b)), getDataAsString: () => f.bytes.toString("utf8") };
    }
    getLastUpdated() { return new Date(this.updated); }
    makeCopy(name, dest) { return dest.createFile(name, this.bytes, this.mime); }
  }
  class Folder {
    constructor(name) { this.name = name; this.files = []; this.folders = []; this.id = `d${++ids}`; }
    getId() { return this.id; }
    getName() { return this.name; }
    getParents() { return iter(this.parent ? [this.parent] : []); }
    getMimeType() { return "folder"; }
    isTrashed() { return false; }
    getFoldersByName(n) { return iter(this.folders.filter((f) => f.name === n)); }
    getFilesByName(n) { return iter(this.files.filter((f) => f.name === n)); }
    getFiles() { return iter(this.files); }
    getFolders() { return iter(this.folders); }
    createFolder(name) { const f = new Folder(name); f.parent = this; this.folders.push(f); return f; }
    /** createFile(name, content, mime) or createFile(blob), as in DriveApp. */
    createFile(name, content, mime) {
      const f = typeof name === "string" ? new File(name, content, mime) : new File(name.getName(), name.bytes, name.mime);
      f.parents.push(this);
      this.files.push(f);
      return f;
    }
    sub(name) { return this.getFoldersByName(name).next(); }
  }
  const myDrive = new Folder("My Drive");
  /** Every file and folder in the tree, for DriveApp.getFileById. */
  const find = (id, folder = myDrive) => {
    const hit = folder.files.find((f) => f.id === id) || (folder.id === id ? folder : null);
    return hit || folder.folders.map((sub) => find(id, sub)).find(Boolean) || null;
  };
  return { File, Folder, myDrive, find };
}

/** A blob as Gmail attachments hand them out. */
function blob(name, bytes, mime = "application/pdf") {
  return {
    name, bytes, mime,
    getName() { return this.name; },
    getContentType() { return this.mime; },
    getBytes() { return Array.from(this.bytes, (b) => (b > 127 ? b - 256 : b)); },
    getDataAsString() { return Buffer.from(this.bytes).toString("utf8"); },
    setName(n) { this.name = n; return this; },
    copyBlob() { return blob(this.name, this.bytes, this.mime); },
  };
}

/**
 * A tiny Gmail API (the Advanced Gmail service, Gmail.Users.*). Each message is given as
 * { id, from, subject, auth, attachments: [blob], body, html } and served as the API would: a MIME
 * payload with headers, base64url bodies, and attachments fetched by attachmentId.
 * Searches honour "-label:" only; the script's message-ID memory does the rest of the deduping.
 * `threads[i].labels` collects the label names applied; `threads[i].onModify` sees each modify call.
 */
function makeGmail(threads) {
  const labels = [];
  const queries = [];
  const stored = {};
  let n = 0;
  const b64 = (x) => Buffer.from(x).toString("base64url");
  const payloadOf = (m) => {
    const headers = [{ name: "From", value: m.from }, { name: "To", value: "intake@example.test" },
      { name: "Subject", value: m.subject || "Close package" }];
    if (m.auth) headers.unshift({ name: "Authentication-Results", value: m.auth });
    const parts = [{ mimeType: "text/plain", filename: "", body: { data: b64(m.body || "") } }];
    if (m.html) parts.push({ mimeType: "text/html", filename: "", body: { data: b64(m.html) } });
    for (const a of m.attachments || []) {
      const id = `att${++n}`;
      stored[id] = b64(a.bytes);
      parts.push({ mimeType: a.mime, filename: a.name, headers: a.headers || [], body: { attachmentId: id, size: a.bytes.length } });
    }
    return { mimeType: "multipart/mixed", filename: "", headers, parts };
  };
  const records = threads.map((msgs, i) => ({
    id: `thread${i}`, labels: [], onModify: null,
    messages: msgs.map((m) => ({ id: m.id, threadId: `thread${i}`, internalDate: String(Date.parse("2026-09-24T12:00:00Z")),
      payload: payloadOf(m), labels: [] })),
  }));
  const byId = Object.fromEntries(records.map((r) => [r.id, r]));
  const Gmail = {
    Users: {
      Threads: {
        list: (user, opts) => {   // paged like the real API: maxResults per page, nextPageToken while more remain
          queries.push(opts.q);
          // Like Gmail, "-label:x" leaves out threads carrying that label (searches write "/" as "-");
          // every other search term is ignored, so the script's own checks still see everything.
          // A thread matches when at least one of its messages carries none of the excluded labels.
          const without = [...opts.q.matchAll(/-label:(\S+)/g)].map((m) => m[1]);
          const has = (names, l) => names.some((n) => n.replace(/\//g, "-") === l);
          const hits = records.filter((r) => r.messages.some((m) =>
            !without.some((l) => has(r.labels, l) || has(m.labels, l))));
          const from = Number(opts.pageToken || 0);
          const page = hits.slice(from, from + (opts.maxResults || 100));
          const out = page.length ? { threads: page.map((r) => ({ id: r.id })) } : {};
          if (from + page.length < hits.length) out.nextPageToken = String(from + page.length);
          return out;
        },
        get: (user, id) => ({ id, messages: byId[id].messages }),
        modify: (req, user, id) => {
          const r = byId[id];
          if (r.onModify) r.onModify(req);
          r.labels.push(...req.addLabelIds.map((l) => labels.find((x) => x.id === l).name));
          return {};
        },
      },
      Messages: {
        Attachments: { get: (user, messageId, attId) => ({ data: stored[attId] }) },
        modify: (req, user, id) => {
          const m = records.flatMap((r) => r.messages).find((x) => x.id === id);
          m.labels.push(...req.addLabelIds.map((l) => labels.find((x) => x.id === l).name));
          return {};
        },
      },
      Labels: {
        list: () => ({ labels: labels.slice() }),
        create: (res) => { const l = { id: `Label_${labels.length + 1}`, name: res.name }; labels.push(l); return l; },
      },
    },
  };
  return { threads: records, queries, labels, Gmail };
}

function load(drive, props = { INTAKE_ADDRESS: "intake@example.test", ROOT_FOLDER: "PnLAnalyze" }, extra = {}) {
  const logs = [];
  const digest = (alg, bytes) => Array.from(crypto.createHash(alg).update(Buffer.from(bytes.map((b) => b & 255))).digest(), (b) => (b > 127 ? b - 256 : b));
  const ctx = vm.createContext({
    DriveApp: {
      getRootFolder: () => drive.myDrive,
      getFileById: (id) => { const f = drive.find(id); if (!f) throw new Error(`No item with the given ID: ${id}`); return f; },
    },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = v; },
      deleteProperty: (k) => { delete props[k]; } }) },
    Logger: { log: (fmt, ...a) => logs.push(String(fmt).replace(/%s/g, () => String(a.shift()))) },  // %s as in Apps Script
    Utilities: {
      DigestAlgorithm: { MD5: "md5", SHA_256: "sha256" },
      computeDigest: digest,
      formatDate: (d, tz, pattern) => ({ yyyyMMdd: "20260924", yyyyMMdd_HHmmss: "20260924_000000" }[pattern] || "2026-09-24T00:00:00-05:00"),
      base64DecodeWebSafe: (str) => Array.from(Buffer.from(str, "base64url"), (b) => (b > 127 ? b - 256 : b)),
      newBlob: (bytes, mime, name) => blob(name, Buffer.from(bytes.map((b) => b & 255)), mime),
    },
    Session: { getScriptTimeZone: () => "UTC" },
    MimeType: { CSV: "text/csv", PLAIN_TEXT: "text/plain", FOLDER: "folder" },
    ...extra,
  });
  vm.runInContext(CODE, ctx);
  ctx.logs = logs;
  return ctx;
}

module.exports = { hex, makeDrive, makeGmail, blob, load };
