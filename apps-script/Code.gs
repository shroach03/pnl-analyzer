/**
 * P&L Analyzer: Gmail → Drive intake script (the "dumb half")
 * ------------------------------------------------------------
 * Saves accountant PDF attachments into <ROOT>/Inbox/ with collision-proof
 * provenance names, logs every file to _manifest.csv (incl. SHA-256), and labels
 * processed threads so nothing is pulled twice. Store/type/period
 * identification is the agent's job, from document content
 * (see agent/procedures/sweep_procedure.md).
 *
 * It also runs cleanupInbox(): trashes Inbox files the agent sweep has
 * certified as fully dispositioned, via _processed_*.json lists the agent
 * writes into Inbox/. The script makes NO decisions: it executes the agent's
 * list verbatim, with safety checks.
 *
 * SETUP (one time):
 *   1. Project Settings → Script Properties. Add:
 *        INTAKE_ADDRESS   the address accountant mail is forwarded to,
 *                         e.g. you+pnl@gmail.com            (required)
 *        PROCESSED_LABEL  Gmail label for handled threads    (default pnl/ingested)
 *        ROOT_FOLDER      top-level Drive folder name        (default PnLAnalyze)
 *   2. Run setup() once and approve the OAuth scopes. It builds the Drive
 *      folder tree, the Gmail label, the manifest, and a 30-minute trigger.
 *   Nothing environment-specific lives in this file.
 *
 * IDEMPOTENCY (two layers):
 *   - Thread layer: the search excludes the processed label (Gmail search
 *     writes nested-label slashes as hyphens: pnl/ingested → label:pnl-ingested).
 *   - Message layer: every processed Gmail message ID is remembered in
 *     Script Properties, so a NEW message landing on an already-labeled
 *     thread (a resend replying to the original email) is still caught by
 *     the catch-up query, while old messages are never re-saved.
 *
 * CLEANUP CONTRACT (agent ↔ script):
 *   - The agent writes Inbox/_processed_{stamp}.json:
 *       { "written_by": "agent sweep", "sweep_date": "...",
 *         "trash": [ { "drive_file_id": "...", "title": "...",
 *                      "reason": "filed|duplicate|quarantined|..." } ] }
 *   - cleanupInbox() trashes each listed file IF AND ONLY IF:
 *       (a) it is not _manifest.csv,
 *       (b) it is not a folder,
 *       (c) it lives inside the project's Drive tree.
 *     Everything goes to Drive TRASH (30-day recovery), never hard-deleted.
 *   - Failures are written to Inbox/_cleanup_error_{stamp}.txt for the next
 *     agent sweep to surface. The _processed file itself is trashed after
 *     execution either way (errors live in the error file, so a bad list is
 *     never retried blindly forever).
 */

// ───────────────────────────── CONFIG ─────────────────────────────
var CONFIG = {
  DEFAULT_LABEL: 'pnl/ingested',
  DEFAULT_ROOT: 'PnLAnalyze',
  // Folders setup() creates under the root. Inbox is where this script writes;
  // the agent owns the rest.
  SUBFOLDERS: ['Inbox', 'Stores', 'Quarantine', 'Reports', 'Portfolio'],
  MANIFEST_NAME: '_manifest.csv',
  MANIFEST_HEADER: 'received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,sha256,note',
  MSG_ID_PROP: 'PNL_PROCESSED_MSG_IDS', // Script Properties key
  MSG_ID_KEEP: 2000,                    // remember this many recent IDs
  PROCESSED_PREFIX: '_processed_',      // agent-written cleanup lists
  CLEANUP_ERROR_PREFIX: '_cleanup_error_' // script-written failure notes
};

/** The Drive root folder name: the ROOT_FOLDER Script Property, else the default. Needs no other setting. */
function rootName_() {
  return PropertiesService.getScriptProperties().getProperty('ROOT_FOLDER') || CONFIG.DEFAULT_ROOT;
}

/** Environment settings, read from Script Properties at run time. */
function settings_() {
  var p = PropertiesService.getScriptProperties();
  var address = p.getProperty('INTAKE_ADDRESS');
  if (!address) throw new Error('Set the INTAKE_ADDRESS Script Property first (see SETUP).');
  var label = p.getProperty('PROCESSED_LABEL') || CONFIG.DEFAULT_LABEL;
  var searchLabel = label.replace(/\//g, '-');
  return {
    label: label,
    root: rootName_(),
    query: 'to:' + address + ' has:attachment -label:' + searchLabel,
    // Catch-up: re-scan recent mail EVEN IF the thread is already labeled, so
    // a resend inside a labeled thread is not lost. Message-ID memory prevents
    // duplicates.
    catchupQuery: 'to:' + address + ' newer_than:7d'
  };
}

// ─────────────────────────── ENTRY POINTS ───────────────────────────

/** Run once by hand. Creates the folder tree, label, manifest, and the trigger. */
function setup() {
  var s = settings_();
  setupDrive();
  getOrCreateLabel_(s.label);
  getOrCreateManifest_();
  // Remove any prior triggers on processInbox, then install a fresh one.
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processInbox') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('processInbox').timeBased().everyMinutes(30).create();
  Logger.log('Setup complete: folder tree, label, manifest, 30-min trigger.');
}

/**
 * Builds the Drive tree. Safe to re-run: existing folders are reused, never
 * duplicated. Folders are located by name under the root, so no folder IDs
 * need to be stored anywhere.
 *
 *   <ROOT>/
 *     Inbox/        this script writes here; the agent sweeps it
 *     Stores/       Stores/SXX/YYYY-MM/SXX_{FR|GL|BR}_YYYY-MM.pdf (the archive)
 *     Quarantine/   documents the agent could not identify
 *     Reports/      one consolidated portfolio report per month
 *     Portfolio/    dashboards and cross-store views
 */
function setupDrive() {
  var root = getOrCreateChild_(DriveApp.getRootFolder(), settings_().root);
  CONFIG.SUBFOLDERS.forEach(function (name) { getOrCreateChild_(root, name); });
  Logger.log('Drive tree ready under /%s.', root.getName());
  return root;
}

/** Turn the scheduler OFF. Nothing is lost: unprocessed mail just waits. */
function pauseIntake() {
  var removed = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processInbox') { ScriptApp.deleteTrigger(t); removed++; }
  });
  Logger.log(removed ? 'Scheduler paused (trigger removed).' : 'Scheduler was already off.');
}

/** Turn the scheduler back ON (30-min cadence). Safe to run repeatedly. */
function resumeIntake() {
  pauseIntake(); // clear any existing trigger so they never stack
  ScriptApp.newTrigger('processInbox').timeBased().everyMinutes(30).create();
  Logger.log('Scheduler on: processInbox every 30 minutes.');
}

/** Main entry: runs on the timer. Safe to also run by hand any time. */
function processInbox() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) return; // a previous run is still going
  try {
    var s = settings_();
    var inbox = getInboxFolder_();

    // 0) Execute any agent-certified cleanup lists BEFORE pulling new mail.
    cleanupInbox_(inbox);

    var label = getOrCreateLabel_(s.label);
    var seen = loadSeenIds_();
    var stats = { messages: 0, pdfs: 0, linkOnly: 0 };

    // Pass 1: unlabeled threads with attachments (the normal flow).
    // Pass 2: recent mail regardless of label (catches resends on
    //         already-labeled threads). Message-ID memory dedupes.
    [s.query, s.catchupQuery].forEach(function (query) {
      GmailApp.search(query, 0, 100).forEach(function (thread) {
        var touched = false;
        thread.getMessages().forEach(function (msg) {
          if (seen.ids[msg.getId()]) return;      // already handled
          handleMessage_(msg, thread, inbox, stats);
          seen.ids[msg.getId()] = true;
          seen.order.push(msg.getId());
          touched = true;
        });
        if (touched) thread.addLabel(label);
      });
    });

    saveSeenIds_(seen);
    Logger.log('Run done: %s new message(s), %s PDF(s) saved, %s link-only note(s).',
      stats.messages, stats.pdfs, stats.linkOnly);
  } finally {
    lock.releaseLock();
  }
}

/** Manual entry point for cleanup only (e.g., right after an agent sweep). */
function cleanupInbox() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30 * 1000)) return;
  try {
    cleanupInbox_(getInboxFolder_());
  } finally {
    lock.releaseLock();
  }
}

// ─────────────────────────── CLEANUP LOGIC ───────────────────────────

/**
 * Executes agent-written _processed_*.json lists: trashes each listed
 * file after safety checks. Decides nothing: the list is the decision.
 */
function cleanupInbox_(inbox) {
  var rootId = getRootId_(inbox);
  var lists = [];
  var it = inbox.getFiles();
  while (it.hasNext()) {
    var f = it.next();
    if (f.getName().indexOf(CONFIG.PROCESSED_PREFIX) === 0 &&
        /\.json$/i.test(f.getName())) lists.push(f);
  }
  if (!lists.length) return;

  var trashed = 0, errors = [];

  lists.forEach(function (listFile) {
    var entries;
    try {
      var parsed = JSON.parse(listFile.getBlob().getDataAsString());
      entries = parsed && parsed.trash;
      if (!Array.isArray(entries)) throw new Error('no "trash" array');
    } catch (e) {
      errors.push(listFile.getName() + ': unreadable/invalid JSON: ' + e.message);
      listFile.setTrashed(true); // never retry a bad list blindly
      return;
    }

    entries.forEach(function (entry) {
      var id = entry && entry.drive_file_id;
      if (!id) { errors.push('entry missing drive_file_id: ' + JSON.stringify(entry)); return; }
      var file;
      try { file = DriveApp.getFileById(id); }
      catch (e) { errors.push(id + ' (' + (entry.title || '?') + '): not found: ' + e.message); return; }

      // Safety checks: the only judgment the script exercises.
      if (file.getName() === CONFIG.MANIFEST_NAME) {
        errors.push(id + ': refused, is ' + CONFIG.MANIFEST_NAME); return;
      }
      if (file.getMimeType() === MimeType.FOLDER) {
        errors.push(id + ' (' + file.getName() + '): refused, is a folder'); return;
      }
      if (!isInTree_(file, rootId)) {
        errors.push(id + ' (' + file.getName() + '): refused, outside the project tree'); return;
      }
      if (file.isTrashed()) return; // already done: idempotent

      try { file.setTrashed(true); trashed++; }
      catch (e) { errors.push(id + ' (' + file.getName() + '): trash failed: ' + e.message); }
    });

    // The list has been executed (successes trashed, failures recorded):
    // retire it so it is never re-run.
    try { listFile.setTrashed(true); } catch (e) {
      errors.push(listFile.getName() + ': could not retire list: ' + e.message);
    }
  });

  if (errors.length) {
    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd_HHmmss');
    inbox.createFile(
      CONFIG.CLEANUP_ERROR_PREFIX + stamp + '.txt',
      'cleanupInbox() completed with ' + errors.length + ' error(s). Surface these at the top of the next agent sweep:\n\n' +
        errors.map(function (e) { return '- ' + e; }).join('\n') + '\n',
      MimeType.PLAIN_TEXT
    );
  }
  Logger.log('cleanupInbox: %s list(s) executed, %s file(s) trashed, %s error(s).',
    lists.length, trashed, errors.length);
}

/** The project root is Inbox's parent. */
function getRootId_(inbox) {
  var parents = inbox.getParents();
  return parents.hasNext() ? parents.next().getId() : null;
}

/** True if walking the file's parent chain reaches rootId (≤10 hops). */
function isInTree_(file, rootId) {
  if (!rootId) return false;
  var frontier = [file];
  for (var depth = 0; depth < 10 && frontier.length; depth++) {
    var next = [];
    for (var i = 0; i < frontier.length; i++) {
      var parents = frontier[i].getParents();
      while (parents.hasNext()) {
        var p = parents.next();
        if (p.getId() === rootId) return true;
        next.push(p);
      }
    }
    frontier = next;
  }
  return false;
}

// ─────────────────────────── CORE LOGIC ───────────────────────────

/** Save one message's PDFs (or a LINK_ONLY note) + manifest rows. */
function handleMessage_(msg, thread, inbox, stats) {
  stats.messages++;
  var msgId = msg.getId();
  var msgIdShort = msgId.slice(-8);
  var received = msg.getDate();
  var dateStamp = Utilities.formatDate(received, Session.getScriptTimeZone(), 'yyyyMMdd');
  var subject = msg.getSubject() || '(no subject)';
  var fromAddr = msg.getFrom() || '(unknown sender)';

  // Real attachments only: skip inline images/signatures.
  var pdfs = msg.getAttachments({ includeInlineImages: false, includeAttachments: true })
    .filter(function (a) {
      return a.getContentType() === 'application/pdf' ||
             /\.pdf$/i.test(a.getName() || '');
    });

  if (pdfs.length === 0) {
    // No PDF attachments → LINK_ONLY note so the agent sweep sees it.
    var noteName = 'LINK_ONLY_' + dateStamp + '_' + msgIdShort + '.txt';
    var noteBody =
      'LINK-ONLY / NO-PDF EMAIL, flagged for the agent intake sweep\n' +
      '--------------------------------------------------------------\n' +
      'Received : ' + received.toString() + '\n' +
      'From     : ' + msg.getFrom() + '\n' +
      'To       : ' + msg.getTo() + '\n' +
      'Subject  : ' + subject + '\n' +
      'Msg ID   : ' + msgId + '\n' +
      'Thread   : ' + thread.getPermalink() + '\n' +
      '--------------------------------------------------------------\n' +
      'Body (first 4000 chars; any download links will appear below):\n\n' +
      (msg.getPlainBody() || '').slice(0, 4000) + '\n';
    var noteFile = inbox.createFile(noteName, noteBody, MimeType.PLAIN_TEXT);
    appendManifestRow_([nowIso_(), msgId, fromAddr, subject, noteName, noteFile.getId(), '', 'link_only']);
    stats.linkOnly++;
    return;
  }

  pdfs.forEach(function (att) {
    var original = sanitizeName_(att.getName() || 'unnamed.pdf');
    var saveName = dateStamp + '_' + msgIdShort + '_' + original;
    if (!/\.pdf$/i.test(saveName)) saveName += '.pdf';
    var blob = att.copyBlob().setName(saveName);
    var file = inbox.createFile(blob);
    var sha256 = sha256Hex_(blob.getBytes());
    appendManifestRow_([nowIso_(), msgId, fromAddr, subject, original, file.getId(), sha256, '']);
    stats.pdfs++;
  });
}

// ─────────────────────────── DRIVE HELPERS ───────────────────────────

function getOrCreateChild_(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function getInboxFolder_() {
  var root = getOrCreateChild_(DriveApp.getRootFolder(), settings_().root);
  return getOrCreateChild_(root, 'Inbox');
}

function getOrCreateManifest_() {
  var inbox = getInboxFolder_();
  var it = inbox.getFilesByName(CONFIG.MANIFEST_NAME);
  if (it.hasNext()) return it.next();
  return inbox.createFile(CONFIG.MANIFEST_NAME, CONFIG.MANIFEST_HEADER + '\n', MimeType.CSV);
}

function appendManifestRow_(cells) {
  var manifest = getOrCreateManifest_();
  var row = cells.map(csvEscape_).join(',');
  var content = manifest.getBlob().getDataAsString();
  if (content.length && content.slice(-1) !== '\n') content += '\n';
  manifest.setContent(content + row + '\n');
}

// ─────────────────────────── GMAIL HELPERS ───────────────────────────

function getOrCreateLabel_(name) {
  return GmailApp.getUserLabelByName(name) || GmailApp.createLabel(name);
}

function loadSeenIds_() {
  var raw = PropertiesService.getScriptProperties().getProperty(CONFIG.MSG_ID_PROP);
  var order = raw ? JSON.parse(raw) : [];
  var ids = {};
  order.forEach(function (id) { ids[id] = true; });
  return { ids: ids, order: order };
}

function saveSeenIds_(seen) {
  var order = seen.order.slice(-CONFIG.MSG_ID_KEEP); // keep most recent N
  PropertiesService.getScriptProperties()
    .setProperty(CONFIG.MSG_ID_PROP, JSON.stringify(order));
}

// ─────────────────── ONE-TIME MIGRATION: MD5 → SHA-256 ───────────────────
//
// Existing deployments have an _manifest.csv whose hash column is `md5`. New
// rows are written as `sha256`, so the manifest and the agent's intake log must
// be converted once, or re-sent files would stop being recognised as duplicates.
//
// Run in this order (details in apps-script/README.md):
//   1. pauseIntake()               nothing may write to the manifest mid-migration
//   2. deploy this version of Code.gs
//   3. previewSha256Migration()    hashes every archived PDF; writes a preview
//                                  and a md5 -> sha256 map next to the Inbox.
//                                  Changes nothing that already exists.
//   4. apply the map to the agent's intake_log.json (tools/migrate_intake_log.py)
//   5. applySha256Migration()      backs the manifest up, then swaps the preview in
//   6. resumeIntake()
//
// Archived copies in Stores/ and Quarantine/ are byte-identical to the Inbox
// originals that cleanup trashed, so they supply the SHA-256 of every file that
// was ever filed or quarantined. Skipped duplicates keep their hash too, because
// they share an MD5 with the copy that was filed. A manifest row whose file was
// never archived is left blank and noted.

var MIGRATION = {
  PREVIEW_NAME: '_manifest_sha256_preview.csv',
  MAP_NAME: 'hash_migration_map.json',
  BACKUP_NAME: '_manifest_md5_backup.csv',
  MAX_MS: 5 * 60 * 1000 // stay inside Apps Script's 6-minute execution limit
};

/** Dry run: builds the preview manifest and the hash map. Touches nothing that already exists. */
function previewSha256Migration() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) { Logger.log('Another run holds the lock. Try again in a minute.'); return; }
  try {
    var started = Date.now();
    var root = findRoot_();
    var manifest = findManifest_(root);
    var rows = parseCsv_(manifest.getBlob().getDataAsString());

    var pairs = {};
    var hashed = 0;
    ['Stores', 'Quarantine', 'Inbox'].forEach(function (name) {
      var folder = findChild_(root, name);
      if (!folder) return;
      collectFiles_(folder, []).forEach(function (file) {
        if (!/\.pdf$/i.test(file.getName())) return;
        if (Date.now() - started > MIGRATION.MAX_MS) {
          throw new Error('Out of time after ' + hashed + ' files. Nothing was written; run it again.');
        }
        var bytes = file.getBlob().getBytes();
        var md5 = digestHex_(Utilities.DigestAlgorithm.MD5, bytes);
        var sha = sha256Hex_(bytes);
        if (pairs[md5] && pairs[md5] !== sha) {
          throw new Error('Two different files share MD5 ' + md5 + '. Stop and investigate before migrating.');
        }
        pairs[md5] = sha;
        hashed++;
      });
    });

    var result = migrateManifestRows_(rows, pairs);
    upsertFile_(root, MIGRATION.PREVIEW_NAME, serializeCsv_(result.rows), MimeType.CSV);
    upsertFile_(root, MIGRATION.MAP_NAME, JSON.stringify({
      generated: nowIso_(),
      manifest_last_updated: manifest.getLastUpdated().getTime(),
      files_hashed: hashed,
      manifest: result.stats,
      pairs: pairs
    }, null, 2), MimeType.PLAIN_TEXT);
    Logger.log('Hashed %s archived PDFs. Manifest rows: %s mapped, %s already SHA-256, %s link-only, %s unmapped.',
      hashed, result.stats.mapped, result.stats.kept, result.stats.blank, result.stats.unmapped);
    Logger.log('Wrote %s and %s to /%s. The live manifest is unchanged.', MIGRATION.PREVIEW_NAME, MIGRATION.MAP_NAME, root.getName());
  } finally {
    lock.releaseLock();
  }
}

/** Swap the previewed manifest in, after saving an exact copy of the original. */
function applySha256Migration() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) { Logger.log('Another run holds the lock. Try again in a minute.'); return; }
  try {
    var root = findRoot_();
    var manifest = findManifest_(root);
    var previewIt = root.getFilesByName(MIGRATION.PREVIEW_NAME);
    var mapIt = root.getFilesByName(MIGRATION.MAP_NAME);
    if (!previewIt.hasNext() || !mapIt.hasNext()) throw new Error('Run previewSha256Migration() first.');
    var preview = previewIt.next();
    var meta = JSON.parse(mapIt.next().getBlob().getDataAsString());
    if (manifest.getLastUpdated().getTime() !== meta.manifest_last_updated) {
      throw new Error('The manifest changed after the preview was made. Run previewSha256Migration() again.');
    }
    var current = parseCsv_(manifest.getBlob().getDataAsString());
    if (current[0].indexOf('md5') < 0) throw new Error('The manifest header has no md5 column; it looks migrated already.');

    manifest.makeCopy(MIGRATION.BACKUP_NAME, root); // exact copy of the original, kept in the root folder
    var content = preview.getBlob().getDataAsString();
    manifest.setContent(content);

    var after = parseCsv_(manifest.getBlob().getDataAsString());
    if (after[0].join(',') !== CONFIG.MANIFEST_HEADER || after.length !== current.length) {
      throw new Error('Verification failed after the swap. The original is saved as ' + MIGRATION.BACKUP_NAME + ' in /' + root.getName() + '.');
    }
    Logger.log('Manifest migrated (%s rows). Original saved as %s. Preview and map files can be deleted once the intake log is done.',
      after.length - 1, MIGRATION.BACKUP_NAME);
  } finally {
    lock.releaseLock();
  }
}

/**
 * Convert manifest rows from the md5 layout to the sha256 layout.
 * `pairs` maps md5 -> sha256 for every archived file. Pure function (no Drive access).
 * A value already 64 hex characters is kept: rows the new script wrote before the migration.
 */
function migrateManifestRows_(rows, pairs) {
  var header = rows[0].slice();
  var col = header.indexOf('md5');
  var noteCol = header.indexOf('note');
  if (col < 0) throw new Error('Manifest header has no md5 column (already migrated?): ' + header.join(','));
  header[col] = 'sha256';
  var out = [header];
  var stats = { mapped: 0, kept: 0, blank: 0, unmapped: 0 };
  for (var i = 1; i < rows.length; i++) {
    var r = rows[i].slice();
    while (r.length < header.length) r.push('');
    var value = String(r[col] || '').toLowerCase();
    if (!value) {
      stats.blank++; // link-only notes carry no hash
    } else if (/^[0-9a-f]{64}$/.test(value)) {
      stats.kept++;
    } else if (pairs[value]) {
      r[col] = pairs[value];
      stats.mapped++;
    } else {
      r[col] = '';
      r[noteCol] = (r[noteCol] ? r[noteCol] + '; ' : '') + 'sha256 unavailable: original not archived';
      stats.unmapped++;
    }
    out.push(r);
  }
  return { rows: out, stats: stats };
}

/** Parse CSV text into rows of strings: quoted fields, "" escapes, embedded commas and newlines, CRLF or LF. */
function parseCsv_(text) {
  var rows = [], row = [], cell = '', inQuotes = false;
  for (var i = 0; i < text.length; i++) {
    var c = text.charAt(i);
    if (inQuotes) {
      if (c === '"') {
        if (text.charAt(i + 1) === '"') { cell += '"'; i++; } else { inQuotes = false; }
      } else {
        cell += c;
      }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === ',') {
      row.push(cell); cell = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text.charAt(i + 1) === '\n') i++;
      row.push(cell); rows.push(row); row = []; cell = '';
    } else {
      cell += c;
    }
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row); }
  return rows.filter(function (r) { return !(r.length === 1 && r[0] === ''); });
}

function serializeCsv_(rows) {
  return rows.map(function (r) { return r.map(csvEscape_).join(','); }).join('\n') + '\n';
}

function findRoot_() {
  // Migration only needs the folder name, not INTAKE_ADDRESS, and must never create a folder.
  var name = rootName_();
  var it = DriveApp.getRootFolder().getFoldersByName(name);
  if (!it.hasNext()) {
    var set = !!PropertiesService.getScriptProperties().getProperty('ROOT_FOLDER');
    throw new Error('Root folder "' + name + '" not found in My Drive. ' + (set
      ? 'Check that the ROOT_FOLDER Script Property matches the folder name exactly.'
      : 'ROOT_FOLDER is not set, so the default was used. Set the ROOT_FOLDER Script Property to your folder name.'));
  }
  return it.next();
}

function findChild_(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : null;
}

function findManifest_(root) {
  var inbox = findChild_(root, 'Inbox');
  var it = inbox ? inbox.getFilesByName(CONFIG.MANIFEST_NAME) : null;
  if (!it || !it.hasNext()) throw new Error('Inbox/' + CONFIG.MANIFEST_NAME + ' not found.');
  return it.next();
}

function collectFiles_(folder, out) {
  var files = folder.getFiles();
  while (files.hasNext()) out.push(files.next());
  var subs = folder.getFolders();
  while (subs.hasNext()) collectFiles_(subs.next(), out);
  return out;
}

function upsertFile_(folder, name, content, mime) {
  var it = folder.getFilesByName(name);
  if (it.hasNext()) { var f = it.next(); f.setContent(content); return f; }
  return folder.createFile(name, content, mime);
}

// ─────────────────────────── SMALL UTILITIES ───────────────────────────

function sha256Hex_(bytes) {
  return digestHex_(Utilities.DigestAlgorithm.SHA_256, bytes);
}

function digestHex_(algorithm, bytes) {
  return Utilities.computeDigest(algorithm, bytes)
    .map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); })
    .join('');
}

function csvEscape_(v) {
  v = String(v == null ? '' : v);
  // Formula-injection guard: a leading = + - @ or tab would be executed as
  // a live formula if the CSV is opened in Excel/Sheets. Neutralize with a
  // leading apostrophe (spreadsheets treat it as a "literal text" marker).
  if (/^[=+\-@\t\r]/.test(v)) v = "'" + v;
  return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}

function sanitizeName_(name) {
  return name.replace(/[\/\\:*?"<>|]/g, '_').replace(/\s+/g, ' ').trim();
}

function nowIso_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(),
    "yyyy-MM-dd'T'HH:mm:ssXXX");
}
