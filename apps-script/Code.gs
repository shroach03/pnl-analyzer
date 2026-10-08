/**
 * P&L Analyzer: Gmail → Drive intake script (the "dumb half")
 * ------------------------------------------------------------
 * Saves accountant PDF attachments into <ROOT>/Inbox/ with collision-proof
 * provenance names, logs every file to _manifest.csv (incl. SHA-256), and labels
 * processed threads so nothing is pulled twice. Store/type/period
 * identification is the agent's job, from document content
 * (see agent/procedures/sweep_procedure.md).
 *
 * SENDER GATE: the one decision the script makes. Only mail whose bare From
 * address is on ALLOWED_SENDERS AND which passes Gmail's authentication
 * (DMARC, or an aligned DKIM/SPF pass) is saved. Anything else gets a
 * manifest row marked rejected_sender and an entry in that run's ONE
 * REJECTED_SENDERS_{stamp}.txt summary note (headers only, never the body) in
 * <ROOT>/Rejected/, and nothing in Inbox/. Refused mail with NO attachments
 * creates nothing at all (a log line only). Every refused message is labeled
 * pnl/refused, which both searches exclude, so spam is looked at exactly once.
 *
 * NOTE CAP: one run writes at most MAX_NOTES_PER_RUN LINK_ONLY notes. Past the
 * cap it logs a warning and leaves the remaining messages unmarked, so the
 * next run picks them up. PDFs are never capped; refused mail shares one note.
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
 *        ALLOWED_SENDERS  the accountant's sender address(es), comma
 *                         separated; copy them by hand from the
 *                         registry's accountant.email_sender  (required)
 *        PROCESSED_LABEL  Gmail label for handled threads    (default pnl/ingested)
 *        ROOT_FOLDER      top-level Drive folder name        (default PnLAnalyze)
 *   2. Run setup() once and approve the OAuth scopes. It builds the Drive
 *      folder tree, the Gmail label, the manifest, and a 30-minute trigger.
 *   Nothing environment-specific lives in this file.
 *
 * PERMISSIONS: appsscript.json lists the exact OAuth scopes, so Google asks
 * for those and nothing it would otherwise infer:
 *   gmail.modify        read mail and add the processed label. Gmail goes
 *                       through the Advanced Gmail service (Gmail.Users.*),
 *                       not GmailApp, which would demand full mailbox
 *                       control (mail.google.com: send, delete forever).
 *   drive               the project folder tree. drive.file is not enough:
 *                       cleanup and the hash migration read files the agent
 *                       wrote through its own Drive connection (cleanup lists,
 *                       the Stores/ archive), which drive.file can't see.
 *   script.scriptapp    install and remove the 30-minute trigger.
 * Run it from a dedicated Google account that only receives the forwarded
 * accountant mail, so these scopes reach nothing else (see apps-script/README.md).
 *
 * IDEMPOTENCY (two layers):
 *   - Thread layer: the search excludes the processed label (Gmail search
 *     writes nested-label slashes as hyphens: pnl/ingested → label:pnl-ingested).
 *   - Message layer: every processed Gmail message ID is remembered in
 *     <ROOT>/_intake_seen_message_ids.json (the most recent 10,000), so a NEW
 *     message landing on an already-labeled thread (a resend replying to the
 *     original email) is still caught by the catch-up query, while old
 *     messages are never re-saved. It used to be one Script Property, whose
 *     9 KB value limit holds only a few hundred IDs; the first run of this
 *     version copies that property into the file and deletes it.
 *   - Order: the ID file is written (and re-read to verify) BEFORE any thread
 *     is labeled. If the write fails, the run logs the error and stops with
 *     no labels applied: mail is never marked done without being remembered.
 *     (The worst case is a PDF saved twice, which the sweep skips by SHA-256.)
 *
 * CLEANUP CONTRACT (agent ↔ script):
 *   - The agent writes Inbox/_processed_{stamp}.json:
 *       { "written_by": "agent sweep", "sweep_date": "...",
 *         "trash": [ { "drive_file_id": "...", "title": "...",
 *                      "reason": "filed|duplicate|quarantined|..." } ] }
 *   - cleanupInbox() trashes a listed file IF AND ONLY IF it is not a folder,
 *     it sits in exactly one folder, and either:
 *       (a) that folder is Inbox/ itself (not a subfolder), and its name does
 *           not start with '_' (so _manifest.csv, _processed_* and
 *           _cleanup_error_* are never trashed by a list). An Inbox PDF also
 *           needs evidence the script checks itself, not the agent's word:
 *             filed / quarantined / pending_approval: a byte-identical copy
 *               (same SHA-256) exists elsewhere in the project folder;
 *             duplicate: an earlier _manifest.csv row has the same SHA-256;
 *           or
 *       (b) THE ONE EXCEPTION, a replaced original in the archive: the reason
 *           is superseded_old_canonical, the file is a canonical
 *           SXX_{type}_YYYY-MM.pdf directly in this project's
 *           Stores/SXX/YYYY-MM/, that month's superseded/ folder already
 *           holds a byte-identical backup (SXX_{type}_YYYY-MM_superseded_*.pdf),
 *           and the file now holding its name is a correction a person
 *           approved with approveCorrections() (its SHA-256 is recorded in
 *           Script Properties, where the agent can't write).
 *     Anything else (Stores/, Reports/, Quarantine/, Rejected/, other trees,
 *     an Inbox PDF with no saved copy or earlier hash, a superseded original
 *     with no backup or no approval) is refused and written to the error file.
 *
 * APPROVALS (person -> script -> agent):
 *   - A person approves a held correction by moving it from pending/ into
 *     approved/ and then running approveCorrections() in this editor. That
 *     records each approved file's SHA-256 in the PNL_APPROVED_CORRECTIONS
 *     Script Property. The agent can write Drive but not Script Properties,
 *     so it can't approve anything by moving or copying files.
 *   - Every run mirrors the record to <ROOT>/_approved_corrections.json for
 *     the sweep to read. If the mirror on Drive holds a hash the record
 *     doesn't, the script rewrites it and leaves Inbox/_integrity_alert_*.txt. Everything trashed goes to Drive TRASH (30-day recovery),
 *     never hard-deleted.
 *   - Failures are written to Inbox/_cleanup_error_{stamp}.txt for the next
 *     agent sweep to surface. The _processed file itself is trashed after
 *     execution either way (errors live in the error file, so a bad list is
 *     never retried blindly forever).
 */

// ───────────────────────────── CONFIG ─────────────────────────────
var CONFIG = {
  DEFAULT_LABEL: 'pnl/ingested',
  DEFAULT_REFUSED_LABEL: 'pnl/refused', // refused messages; excluded from every search
  MAX_REFUSED_LISTED: 200,              // entries written into one run's summary note
  DEFAULT_ROOT: 'PnLAnalyze',
  // Folders setup() creates under the root. Inbox is where this script writes;
  // the agent owns the rest.
  SUBFOLDERS: ['Inbox', 'Stores', 'Quarantine', 'Rejected', 'Reports', 'Portfolio'],
  REJECTED_FOLDER: 'Rejected',          // headers-only notes for mail the sender gate refused
  MANIFEST_NAME: '_manifest.csv',
  MANIFEST_HEADER: 'received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,sha256,note',
  SEEN_IDS_FILE: '_intake_seen_message_ids.json', // processed message IDs, in the root folder
  MSG_ID_PROP: 'PNL_PROCESSED_MSG_IDS', // the OLD Script Properties key; migrated into SEEN_IDS_FILE
  MSG_ID_KEEP: 10000,                   // remember this many recent IDs
  MAX_NOTES_PER_RUN: 10,                // LINK_ONLY notes one run may write
  SEARCH_PAGE_SIZE: 100,                // threads per Gmail search page
  MAX_SEARCH_PAGES: 50,                 // at most 5,000 threads per search per run
  RUN_BUDGET_MS: 4.5 * 60 * 1000,       // stop taking new threads well inside Apps Script's 6-minute limit
  APPROVALS_PROP: 'PNL_APPROVED_CORRECTIONS', // approved corrections: SHA-256 -> {file, approved_at}
  APPROVALS_FILE: '_approved_corrections.json', // read-only mirror of that record, in the root folder
  APPROVALS_KEEP: 50,                   // most recent approvals kept (a 9 KB property holds ~60)
  INTEGRITY_PREFIX: '_integrity_alert_', // script-written tamper notes, in Inbox/
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
  var allowed = parseAllowedSenders_(p.getProperty('ALLOWED_SENDERS'));
  if (!Object.keys(allowed).length) {
    throw new Error('Set the ALLOWED_SENDERS Script Property first: the accountant\'s address(es) from the registry (see SETUP).');
  }
  var label = p.getProperty('PROCESSED_LABEL') || CONFIG.DEFAULT_LABEL;
  var refusedLabel = p.getProperty('REFUSED_LABEL') || CONFIG.DEFAULT_REFUSED_LABEL;
  var searchLabel = label.replace(/\//g, '-');
  var notRefused = ' -label:' + refusedLabel.replace(/\//g, '-');  // a message refused once is never fetched again
  return {
    label: label,
    refusedLabel: refusedLabel,
    root: rootName_(),
    allowed: allowed,
    query: 'to:' + address + ' has:attachment -label:' + searchLabel + notRefused,
    // Catch-up: re-scan recent mail EVEN IF the thread is already labeled, so
    // a resend inside a labeled thread is not lost, and so a link-only email
    // (which has no attachment for the main query to find) is seen at all.
    // It has no attachment filter, so it is limited to the allowed senders:
    // anyone else's attachment-less mail is never even fetched. Message-ID
    // memory prevents duplicates.
    catchupQuery: 'to:' + address + ' newer_than:7d from:(' + Object.keys(allowed).join(' OR ') + ')' + notRefused
  };
}

// ─────────────────────────── ENTRY POINTS ───────────────────────────

/** Run once by hand. Creates the folder tree, label, manifest, and the trigger. */
function setup() {
  var s = settings_();
  setupDrive();
  getOrCreateLabel_(s.label);
  getOrCreateLabel_(s.refusedLabel);
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
 *     Rejected/     REJECTED_SENDER notes for mail the sender gate refused
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

    // 0) Restore the approvals mirror from the record, then execute any agent-certified cleanup lists
    //    BEFORE pulling new mail.
    syncApprovals_(inbox);
    cleanupInbox_(inbox);

    var label = getOrCreateLabel_(s.label);
    var root = getOrCreateChild_(DriveApp.getRootFolder(), s.root);
    migrateSeenIds_(root);   // one-time, and a no-op once the old property is gone
    var seen = loadSeenIds_(root);
    var toLabel = {};
    var refusedLabel = getOrCreateLabel_(s.refusedLabel);
    var stats = { messages: 0, pdfs: 0, linkOnly: 0, rejected: 0, ignored: 0, notes: 0, deferred: {},
                  refused: [], refusedIds: [] };

    // Pass 1: unlabeled threads with attachments (the normal flow).
    // Pass 2: recent mail regardless of label (catches resends on
    //         already-labeled threads). Message-ID memory dedupes.
    var started = Date.now(), handled = 0, leftOver = 0;
    [s.query, s.catchupQuery].forEach(function (query) {
      searchThreads_(query).forEach(function (thread) {
        // Time budget: past it, leave the rest unlabeled and unremembered for the next run. At least one
        // thread is always handled, so even a slow backlog makes progress.
        if (handled > 0 && Date.now() - started > CONFIG.RUN_BUDGET_MS) { leftOver++; return; }
        handled++;
        var touched = false;
        thread.getMessages().forEach(function (msg) {
          if (seen.ids[msg.getId()]) return;      // already handled
          if (handleMessage_(msg, thread, inbox, stats, s) === 'deferred') return;  // note cap: retry next run
          seen.ids[msg.getId()] = true;
          seen.order.push(msg.getId());
          touched = true;
        });
        if (touched) toLabel[thread.getId()] = thread;
      });
    });

    writeRefusals_(stats.refused, root);   // one summary note + one manifest row per refused message

    // Remember first, label second: a failed save throws here, before any thread is marked done.
    saveSeenIds_(seen, root);
    Object.keys(toLabel).forEach(function (id) { toLabel[id].addLabel(label); });
    stats.refusedIds.forEach(function (id) { labelMessage_(id, refusedLabel); });
    if (leftOver) {
      Logger.log('WARNING: time budget reached after %s thread(s); %s left for the next run.', handled, leftOver);
    }
    var deferred = Object.keys(stats.deferred).length;  // both searches can meet the same message
    if (deferred) {
      Logger.log('WARNING: note cap of %s reached; %s message(s) left for the next run. A burst of link-only or ' +
        'refused mail usually means spam or a misbehaving sender: check the Gmail inbox.', CONFIG.MAX_NOTES_PER_RUN, deferred);
    }
    Logger.log('Run done: %s new message(s), %s PDF(s) saved, %s link-only note(s), %s rejected sender(s), ' +
      '%s ignored (refused, no attachments), %s deferred.',
      stats.messages, stats.pdfs, stats.linkOnly, stats.rejected, stats.ignored, deferred);
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
  var evidence = evidence_(inbox, rootId);   // built lazily, once per cleanup run
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

      // Safety checks: the only judgment the script exercises over a list.
      var refusal = cleanupRefusal_(file, entry, inbox, rootId, evidence);
      if (refusal) {
        errors.push(id + ' (' + file.getName() + ', reason ' + (entry.reason || '?') + '): refused, ' + refusal); return;
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

function parentsOf_(item) {
  var out = [], it = item.getParents();
  while (it.hasNext()) out.push(it.next());
  return out;
}

function soleParent_(item) {
  var parents = parentsOf_(item);
  return parents.length === 1 ? parents[0] : null;
}

/** '' if the listed file may be trashed under the cleanup contract; otherwise why not. */
function cleanupRefusal_(file, entry, inbox, rootId, evidence) {
  if (file.getMimeType() === MimeType.FOLDER) return 'is a folder';
  var parents = parentsOf_(file);
  if (parents.length !== 1) return 'is in ' + parents.length + ' folders; only a file in exactly one folder can be trashed';
  var name = file.getName();
  if (parents[0].getId() === inbox.getId()) {
    if (name.charAt(0) === '_') return 'is an Inbox control file (' + name + ')';
    return /\.pdf$/i.test(name) ? inboxPdfRefusal_(file, entry, evidence) : '';  // notes carry no document
  }
  if (entry.reason !== 'superseded_old_canonical') {
    return 'is not directly inside Inbox/ (outside the Inbox, only a superseded_old_canonical original with a backup may be trashed)';
  }
  return supersededRefusal_(file, parents[0], rootId);
}

/** The one exception: a replaced canonical document whose identical backup is already in superseded/. */
function supersededRefusal_(file, month, rootId) {
  var name = file.getName();
  var m = /^(S\d{2})_(FR|GL|BR\d*)_(\d{4}-\d{2})\.pdf$/.exec(name);
  if (!m) return 'superseded_old_canonical, but ' + name + ' is not a canonical SXX_{type}_YYYY-MM.pdf';
  var store = soleParent_(month);
  var stores = store && soleParent_(store);
  var root = stores && soleParent_(stores);
  if (month.getName() !== m[3] || !store || store.getName() !== m[1] || !stores || stores.getName() !== 'Stores' ||
      !root || root.getId() !== rootId) {
    return 'superseded_old_canonical, but the file is not in this project\'s Stores/' + m[1] + '/' + m[3] + '/';
  }
  var backups = month.getFoldersByName('superseded');
  var want = sha256Hex_(file.getBlob().getBytes());
  var prefix = name.replace(/\.pdf$/, '') + '_superseded_';
  while (backups.hasNext()) {
    var it = backups.next().getFiles();
    while (it.hasNext()) {
      var b = it.next();
      if (b.getName().indexOf(prefix) === 0 && /_superseded_\d{8}\.pdf$/.test(b.getName()) && !b.isTrashed() &&
          sha256Hex_(b.getBlob().getBytes()) === want) return replacementRefusal_(file, month);
    }
  }
  return 'superseded_old_canonical, but no identical backup (' + prefix + 'YYYYMMDD.pdf) is in ' + m[3] +
    '/superseded/; the only copy is never trashed';
}

/** The replaced original may go only if the file now holding its name is an approved correction. */
function replacementRefusal_(file, month) {
  var approved = loadApprovals_();
  var it = month.getFilesByName(file.getName());
  while (it.hasNext()) {
    var other = it.next();
    if (other.getId() !== file.getId() && !other.isTrashed() && approved[sha256Hex_(other.getBlob().getBytes())]) return '';
  }
  return 'superseded_old_canonical, but no approved correction holds ' + file.getName() +
    ' (approvals are recorded only by approveCorrections()); the original is kept';
}

/**
 * What the script can check for itself before trashing an Inbox PDF: every other PDF in the project
 * folder (outside Inbox/), indexed by size and hashed only when a size matches, and the manifest rows.
 */
function evidence_(inbox, rootId) {
  var index = null, rows = null, hashes = {};
  function hashOf(f) { return hashes[f.getId()] || (hashes[f.getId()] = sha256Hex_(f.getBlob().getBytes())); }
  return {
    hashOf: hashOf,
    /** A byte-identical copy of `file` anywhere in the project folder outside Inbox/, or null. */
    copyOf: function (file) {
      if (!index) {
        index = {};
        var root = soleParent_(inbox);
        if (root && root.getId() === rootId) collectFiles_(root, [], inbox.getId()).forEach(function (f) {
          if (f.isTrashed()) return;
          (index[f.getSize()] = index[f.getSize()] || []).push(f);
        });
      }
      var want = hashOf(file);
      return (index[file.getSize()] || []).filter(function (f) { return hashOf(f) === want; })[0] || null;
    },
    /** True if a manifest row before `file`'s own row records the same SHA-256 for a different file. */
    earlierRow: function (file) {
      if (!rows) {
        var it = inbox.getFilesByName(CONFIG.MANIFEST_NAME);
        rows = it.hasNext() ? parseCsv_(it.next().getBlob().getDataAsString()) : [];
      }
      var head = rows[0] || [], idCol = head.indexOf('drive_file_id'), shaCol = head.indexOf('sha256');
      if (idCol < 0 || shaCol < 0) return false;
      var want = hashOf(file), own = -1;
      for (var i = 1; i < rows.length; i++) if (rows[i][idCol] === file.getId()) { own = i; break; }
      var end = own < 0 ? rows.length : own;
      for (var j = 1; j < end; j++) {
        if (rows[j][idCol] !== file.getId() && String(rows[j][shaCol]).toLowerCase() === want) return true;
      }
      return false;
    }
  };
}

function inboxPdfRefusal_(file, entry, evidence) {
  var reason = entry.reason;
  if (reason === 'filed' || reason === 'quarantined' || reason === 'pending_approval') {
    return evidence.copyOf(file) ? '' :
      reason + ', but no identical copy (same SHA-256) exists elsewhere in the project folder; the only copy is never trashed';
  }
  if (reason === 'duplicate') {
    return evidence.earlierRow(file) ? '' : 'duplicate, but no earlier _manifest.csv row has its SHA-256';
  }
  return 'reason ' + (reason || '(none)') + ' cannot certify an Inbox PDF';
}

// ─────────────────────────── APPROVALS ───────────────────────────

/** SHA-256 -> {file, approved_at} for every correction a person approved. Never read from Drive. */
function loadApprovals_() {
  var raw = PropertiesService.getScriptProperties().getProperty(CONFIG.APPROVALS_PROP);
  return raw ? JSON.parse(raw) : {};
}

/**
 * Run by hand, by a person, after moving a held correction from pending/ into approved/. Records the
 * SHA-256 of every PDF now in a Stores/SXX/YYYY-MM/approved/ folder, logs each one so you can check it
 * is what you meant to approve, and refreshes the mirror the sweep reads.
 */
function approveCorrections() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) { Logger.log('Another run holds the lock. Try again in a minute.'); return; }
  try {
    var root = getOrCreateChild_(DriveApp.getRootFolder(), rootName_());
    var approved = loadApprovals_();
    var added = 0;
    var stores = findChild_(root, 'Stores');
    var found = stores ? collectFiles_(stores, []) : [];
    found.forEach(function (f) {
      var month = soleParent_(f);
      if (f.isTrashed() || !month || month.getName() !== 'approved' || !/\.pdf$/i.test(f.getName())) return;
      var sha = sha256Hex_(f.getBlob().getBytes());
      if (approved[sha]) return;
      approved[sha] = { file: f.getName(), approved_at: nowIso_() };
      added++;
      Logger.log('Approved %s (SHA-256 %s). If you did not move this file into approved/, run revokeApprovals() now.',
        f.getName(), sha.slice(0, 16));
    });
    var keys = Object.keys(approved).sort(function (a, b) { return approved[a].approved_at < approved[b].approved_at ? -1 : 1; });
    keys.slice(0, Math.max(0, keys.length - CONFIG.APPROVALS_KEEP)).forEach(function (k) { delete approved[k]; });
    PropertiesService.getScriptProperties().setProperty(CONFIG.APPROVALS_PROP, JSON.stringify(approved));
    writeApprovalsMirror_(root, approved);
    Logger.log('%s new correction(s) approved; the next sweep applies them.', added);
  } finally {
    lock.releaseLock();
  }
}

/** Run by hand to withdraw every approval not yet applied (e.g. after approving the wrong file). */
function revokeApprovals() {
  PropertiesService.getScriptProperties().deleteProperty(CONFIG.APPROVALS_PROP);
  writeApprovalsMirror_(getOrCreateChild_(DriveApp.getRootFolder(), rootName_()), {});
  Logger.log('All approvals withdrawn.');
}

function writeApprovalsMirror_(root, approved) {
  upsertFile_(root, CONFIG.APPROVALS_FILE, JSON.stringify({
    written_by: 'approveCorrections (Apps Script)',
    note: 'A copy of the approval record kept in Script Properties. Edits here are undone and reported.',
    updated: nowIso_(), approved: approved }, null, 2), MimeType.PLAIN_TEXT);
}

/** Make the Drive mirror match the record. A hash on Drive that the record lacks is reported as tampering. */
function syncApprovals_(inbox) {
  var root = soleParent_(inbox);
  if (!root) return;
  var approved = loadApprovals_();
  var it = root.getFilesByName(CONFIG.APPROVALS_FILE);
  var exists = it.hasNext(), mirror = {};
  if (exists) {
    try { mirror = JSON.parse(it.next().getBlob().getDataAsString()).approved || {}; } catch (e) { mirror = { '(unreadable file)': true }; }
  }
  var forged = Object.keys(mirror).filter(function (k) { return !approved[k]; });
  var stale = Object.keys(approved).some(function (k) { return !mirror[k]; });
  if (forged.length) {
    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd_HHmmss');
    inbox.createFile(CONFIG.INTEGRITY_PREFIX + stamp + '.txt',
      'INTEGRITY ALERT: ' + CONFIG.APPROVALS_FILE + ' listed ' + forged.length + ' approval(s) that no person recorded ' +
      '(approvals come only from approveCorrections()). The file has been restored from the record. Treat any ' +
      'correction applied from these hashes as unapproved, and check who changed the file:\n\n' +
      forged.map(function (k) { return '- ' + k; }).join('\n') + '\n', MimeType.PLAIN_TEXT);
    Logger.log('INTEGRITY ALERT: %s forged approval(s) removed from %s.', forged.length, CONFIG.APPROVALS_FILE);
  }
  if (forged.length || stale) writeApprovalsMirror_(root, approved);
}

// ─────────────────────────── CORE LOGIC ───────────────────────────

/**
 * Save one message's PDFs (or a LINK_ONLY note) + manifest rows. A refused sender gets a REJECTED_SENDER
 * note instead, unless the message has no attachments at all, which creates nothing.
 * Returns 'deferred' when the note cap stopped it (the caller leaves it unmarked), else 'done'.
 */
function handleMessage_(msg, thread, inbox, stats, s) {
  var msgId = msg.getId();
  var msgIdShort = msgId.slice(-8);
  var received = msg.getDate();
  var dateStamp = Utilities.formatDate(received, Session.getScriptTimeZone(), 'yyyyMMdd');
  var subject = msg.getSubject() || '(no subject)';
  var fromAddr = msg.getFrom() || '(unknown sender)';

  // Real attachments only: skip inline images/signatures.
  var attachments = msg.getAttachments({ includeInlineImages: false, includeAttachments: true });
  var pdfs = attachments.filter(function (a) {
    return a.getContentType() === 'application/pdf' || /\.pdf$/i.test(a.getName() || '');
  });
  var needsNote = pdfs.length === 0;

  // Sender gate: decided before anything is written to the Inbox.
  var refusal = senderRefusal_(msg, s.allowed);
  if (refusal) stats.refusedIds.push(msgId);  // labeled pnl/refused at the end of the run, so never searched again
  if (refusal && attachments.length === 0) {
    // Nothing was sent that could be a document: create nothing, so plain-text spam leaves no trace in Drive.
    Logger.log('Ignored %s (no attachments): %s', msgId, refusal);
    stats.ignored++;
    return 'done';
  }
  if (!refusal && needsNote && stats.notes >= CONFIG.MAX_NOTES_PER_RUN) {
    stats.deferred[msgId] = true;
    return 'deferred';
  }
  stats.messages++;
  if (refusal) {
    stats.refused.push({ msg: msg, thread: thread, reason: refusal,
      names: attachments.map(function (a) { return a.getName() || '(unnamed)'; }) });
    stats.rejected++;
    return 'done';
  }

  if (needsNote) {
    // No PDF attachments → LINK_ONLY note so the agent sweep sees it.
    // The body is NOT copied: the agent reads this note, and free text from an email is where hidden
    // instructions would ride in. The metadata and the links are all a person needs to fetch the documents.
    var noteName = 'LINK_ONLY_' + dateStamp + '_' + msgIdShort + '.txt';
    var links = extractLinks_((msg.getPlainBody() || '') + '\n' + (msg.getBody() || ''));
    var noteBody =
      'LINK-ONLY / NO-PDF EMAIL, flagged for the agent intake sweep\n' +
      '--------------------------------------------------------------\n' +
      'Received : ' + received.toString() + '\n' +
      'From     : ' + msg.getFrom() + '\n' +
      'Subject  : ' + subject + '\n' +
      'Msg ID   : ' + msgId + '\n' +
      'Thread   : ' + thread.getPermalink() + '\n' +
      '--------------------------------------------------------------\n' +
      'Links found in the email (the body itself is not copied):\n' +
      (links.length ? links.map(function (l) { return '- ' + l; }).join('\n') : '(none)') + '\n';
    var noteFile = inbox.createFile(noteName, noteBody, MimeType.PLAIN_TEXT);
    appendManifestRow_([nowIso_(), msgId, fromAddr, subject, noteName, noteFile.getId(), '', 'link_only']);
    stats.linkOnly++;
    stats.notes++;
    return 'done';
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
  return 'done';
}

// ─────────────────────────── SENDER GATE ───────────────────────────

/** ALLOWED_SENDERS → { 'a@b.com': true }. Comma, semicolon or whitespace separated; case-insensitive. */
function parseAllowedSenders_(raw) {
  var out = {};
  String(raw || '').split(/[\s,;]+/).forEach(function (a) {
    a = bareAddress_(a);
    if (a) out[a] = true;
  });
  return out;
}

/**
 * The bare address from a From header, lowercased: 'Pat <Pat@X.com>' → 'pat@x.com'.
 * Only the address inside the final <...> counts, so a display name that looks
 * like an allowed address ('"closeout@cpa.test" <x@evil.test>') is not taken for one.
 * Returns '' when there is no single well-formed address.
 */
function bareAddress_(from) {
  var s = String(from || '').trim();
  var m = s.match(/<([^<>]*)>\s*$/);
  var addr = (m ? m[1] : s).trim().toLowerCase();
  return /^[^\s@<>",;]+@[^\s@<>",;]+\.[^\s@<>",;]+$/.test(addr) ? addr : '';
}

/** '' if the message may be saved; otherwise why it is refused. */
function senderRefusal_(msg, allowed) {
  var addr = bareAddress_(msg.getFrom());
  if (!addr) return 'no readable sender address';
  if (!allowed[addr]) return 'sender ' + addr + ' is not on ALLOWED_SENDERS';
  var header = '';
  try { header = msg.getHeader('Authentication-Results') || ''; } catch (e) { header = ''; }
  var failure = authFailure_(header, addr.split('@')[1]);
  return failure ? 'sender ' + addr + ' is allowed but failed authentication: ' + failure : '';
}

/**
 * Reads Gmail's Authentication-Results header. Pure: returns '' when the mail
 * authenticates for fromDomain, else the reason it does not.
 *   - Only Gmail's own result counts (authserv-id mx.google.com).
 *   - A DMARC result decides on its own: pass is allowed, anything else is not.
 *   - With no DMARC result, a DKIM or SPF pass counts only if its domain aligns
 *     with the From domain (the same domain, or one a subdomain of the other).
 *   - No header at all is a failure.
 */
function authFailure_(header, fromDomain) {
  var h = String(header || '').replace(/\([^)]*\)/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!h) return 'no Authentication-Results header';
  var parts = h.split(';').map(function (p) { return p.trim(); });
  if (parts[0].split(' ')[0] !== 'mx.google.com') return 'Authentication-Results not written by mx.google.com';
  fromDomain = String(fromDomain || '').toLowerCase();
  var results = [];
  parts.slice(1).forEach(function (p) {
    var m = /^([a-z]+)=([a-z]+)/.exec(p);
    if (m) results.push({ method: m[1], result: m[2], text: p });
  });
  var dmarc = results.filter(function (r) { return r.method === 'dmarc'; })[0];
  if (dmarc) return dmarc.result === 'pass' ? '' : 'DMARC ' + dmarc.result;
  for (var i = 0; i < results.length; i++) {
    var r = results[i];
    if (r.result !== 'pass') continue;
    var prop = r.method === 'dkim' ? /header\.[di]=@?([^\s;]+)/.exec(r.text)
             : r.method === 'spf' ? /smtp\.mailfrom=([^\s;]+)/.exec(r.text) : null;
    if (prop && domainsAlign_(prop[1].split('@').pop(), fromDomain)) return '';
  }
  return 'no DMARC result and no SPF/DKIM pass aligned with ' + fromDomain;
}

function domainsAlign_(a, b) {
  if (!a || !b) return false;
  return a === b || a.slice(-(b.length + 1)) === '.' + b || b.slice(-(a.length + 1)) === '.' + a;
}

/**
 * This run's refused mail: ONE headers-only summary note in Rejected/, and one manifest row per message
 * pointing at it. The bodies and attachments are never saved. A burst of spam costs one file per run.
 */
function writeRefusals_(refused, root) {
  if (!refused.length) return;
  var folder = getOrCreateChild_(root, CONFIG.REJECTED_FOLDER);
  var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyyMMdd_HHmmss');
  var noteName = 'REJECTED_SENDERS_' + stamp + '.txt';
  var listed = refused.slice(0, CONFIG.MAX_REFUSED_LISTED);
  var body =
    'REJECTED_SENDERS: ' + refused.length + ' email(s) refused by the intake sender gate this run. ' +
    'Nothing was saved to Inbox/.\n' +
    'The email bodies and attachments are deliberately not copied. If a sender is legitimate, add the\n' +
    'address to the registry and to ALLOWED_SENDERS, then ask for the email to be re-sent.\n' +
    listed.map(function (r) {
      return '--------------------------------------------------------------\n' +
        'Received : ' + r.msg.getDate().toString() + '\n' +
        'From     : ' + r.msg.getFrom() + '\n' +
        'To       : ' + r.msg.getTo() + '\n' +
        'Subject  : ' + (r.msg.getSubject() || '(no subject)') + '\n' +
        'Msg ID   : ' + r.msg.getId() + '\n' +
        'Thread   : ' + r.thread.getPermalink() + '\n' +
        'Reason   : ' + r.reason + '\n' +
        'Attached : ' + (r.names.length ? r.names.join(', ') : 'none') + ' (not saved)\n';
    }).join('') +
    (refused.length > listed.length ? '... and ' + (refused.length - listed.length) + ' more (see _manifest.csv)\n' : '');
  var file = folder.createFile(noteName, body, MimeType.PLAIN_TEXT);
  refused.forEach(function (r) {
    appendManifestRow_([nowIso_(), r.msg.getId(), r.msg.getFrom() || '(unknown sender)', r.msg.getSubject() || '(no subject)',
      noteName, file.getId(), '', 'rejected_sender: ' + r.reason]);
  });
}

// ─────────────────────────── DRIVE HELPERS ───────────────────────────

function getOrCreateChild_(parent, name) {
  var it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}

function getInboxFolder_() {
  // Only the folder name is needed, so cleanupInbox() runs even before the intake settings are complete.
  var root = getOrCreateChild_(DriveApp.getRootFolder(), rootName_());
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

// Gmail is reached through the Advanced Gmail service (Gmail API v1) so the script needs only the
// gmail.modify scope. These helpers wrap API responses in small objects with the GmailApp-style
// methods the rest of the script uses (getFrom, getAttachments, addLabel, ...).

/** A label as { id, name }: found by name, or created. */
function getOrCreateLabel_(name) {
  var existing = (Gmail.Users.Labels.list('me').labels || []).filter(function (l) { return l.name === name; })[0];
  var label = existing || Gmail.Users.Labels.create({ name: name, labelListVisibility: 'labelShow', messageListVisibility: 'show' }, 'me');
  return { id: label.id, name: label.name };
}

/** Up to 100 threads matching a Gmail search, each loaded lazily. */
/** One message (not its whole thread, which may also hold the accountant's mail) gets a label. */
function labelMessage_(messageId, label) {
  Gmail.Users.Messages.modify({ addLabelIds: [label.id] }, 'me', messageId);
}

/** Every thread matching a Gmail search, page by page (up to MAX_SEARCH_PAGES), each loaded lazily. */
function searchThreads_(query) {
  var ids = [], token = null, pages = 0;
  do {
    var res = Gmail.Users.Threads.list('me', token ? { q: query, maxResults: CONFIG.SEARCH_PAGE_SIZE, pageToken: token }
                                                   : { q: query, maxResults: CONFIG.SEARCH_PAGE_SIZE });
    (res.threads || []).forEach(function (t) { ids.push(t.id); });
    token = res.nextPageToken;
    pages++;
  } while (token && pages < CONFIG.MAX_SEARCH_PAGES);
  if (token) Logger.log('WARNING: search stopped after %s pages (%s threads); the rest wait for the next run.', pages, ids.length);
  return ids.map(wrapThread_);
}

function wrapThread_(threadId) {
  var messages = null;
  return {
    getId: function () { return threadId; },
    getMessages: function () {
      if (!messages) {
        messages = (Gmail.Users.Threads.get('me', threadId, { format: 'full' }).messages || []).map(wrapMessage_);
      }
      return messages;
    },
    addLabel: function (label) { Gmail.Users.Threads.modify({ addLabelIds: [label.id] }, 'me', threadId); },
    getPermalink: function () { return 'https://mail.google.com/mail/u/0/#all/' + threadId; }
  };
}

/** Every MIME part of a message, depth first. */
function mimeParts_(part, out) {
  out = out || [];
  if (!part) return out;
  out.push(part);
  (part.parts || []).forEach(function (p) { mimeParts_(p, out); });
  return out;
}

function partHeader_(part, name) {
  var h = (part.headers || []).filter(function (x) { return x.name.toLowerCase() === name.toLowerCase(); })[0];
  return h ? h.value : '';
}

function decodeText_(data) {
  return data ? Utilities.newBlob(Utilities.base64DecodeWebSafe(data)).getDataAsString() : '';
}

function wrapMessage_(m) {
  var parts = mimeParts_(m.payload);
  var header = function (name) { return partHeader_(m.payload || {}, name); };
  var text = function (mime) {
    var part = parts.filter(function (p) { return p.mimeType === mime && !p.filename; })[0];
    return part ? decodeText_(part.body && part.body.data) : '';
  };
  return {
    getId: function () { return m.id; },
    getDate: function () { return new Date(Number(m.internalDate)); },
    getFrom: function () { return header('From'); },
    getTo: function () { return header('To'); },
    getSubject: function () { return header('Subject'); },
    getHeader: header,   // the first header of that name: for Authentication-Results, Gmail's own
    getPlainBody: function () { return text('text/plain'); },
    getBody: function () { return text('text/html'); },
    /** Real attachments, like GmailApp's getAttachments({ includeInlineImages: false }). */
    getAttachments: function () {
      return parts.filter(function (p) {
        if (!p.filename || !p.body) return false;
        var inlineImage = /^image\//i.test(p.mimeType || '') &&
          (/^inline/i.test(partHeader_(p, 'Content-Disposition')) || partHeader_(p, 'Content-ID'));
        return !inlineImage;
      }).map(function (p) { return wrapAttachment_(m.id, p); });
    }
  };
}

function wrapAttachment_(messageId, part) {
  return {
    getName: function () { return part.filename; },
    getContentType: function () { return part.mimeType; },
    copyBlob: function () {
      var data = part.body.data ||
        Gmail.Users.Messages.Attachments.get('me', messageId, part.body.attachmentId).data;
      return Utilities.newBlob(Utilities.base64DecodeWebSafe(data), part.mimeType, part.filename);
    }
  };
}

/** The processed-message memory, from <ROOT>/_intake_seen_message_ids.json. An unreadable file stops the run. */
function loadSeenIds_(root) {
  var order = [];
  var it = root.getFilesByName(CONFIG.SEEN_IDS_FILE);
  if (it.hasNext()) {
    var file = it.next();
    try {
      order = JSON.parse(file.getBlob().getDataAsString()).ids;
      if (!Array.isArray(order)) throw new Error('no "ids" array');
    } catch (e) {
      // Treating it as empty would re-save every recent email, so stop and let a person look.
      Logger.log('ERROR: %s is unreadable (%s). Intake stopped; fix or restore the file (Drive keeps versions).',
        CONFIG.SEEN_IDS_FILE, e.message);
      throw new Error(CONFIG.SEEN_IDS_FILE + ' is unreadable: ' + e.message);
    }
  }
  var ids = {};
  order.forEach(function (id) { ids[id] = true; });
  return { ids: ids, order: order };
}

/** Write the memory (most recent MSG_ID_KEEP IDs), then re-read it. Any failure is logged and stops the run. */
function saveSeenIds_(seen, root) {
  var order = seen.order.slice(-CONFIG.MSG_ID_KEEP);
  try {
    upsertFile_(root, CONFIG.SEEN_IDS_FILE,
      JSON.stringify({ written_by: 'processInbox', updated: nowIso_(), count: order.length, ids: order }),
      MimeType.PLAIN_TEXT);
    var back = loadSeenIds_(root).order;
    if (back.length !== order.length || back[back.length - 1] !== order[order.length - 1]) {
      throw new Error('re-read ' + back.length + ' IDs, expected ' + order.length);
    }
  } catch (e) {
    Logger.log('ERROR: could not save %s (%s). Intake stopped before labeling any thread; the next run retries.',
      CONFIG.SEEN_IDS_FILE, e.message);
    throw new Error('Saving ' + CONFIG.SEEN_IDS_FILE + ' failed: ' + e.message);
  }
}

/**
 * One-time move of the processed-message IDs from the old Script Property into the ID file.
 * Merges with the file if one exists, verifies the file holds every ID, and only then deletes
 * the property. Runs automatically at the start of processInbox(); safe to run by hand any time.
 */
function migrateSeenIdsToFile() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) { Logger.log('Another run holds the lock. Try again in a minute.'); return; }
  try {
    migrateSeenIds_(getOrCreateChild_(DriveApp.getRootFolder(), rootName_()));
  } finally {
    lock.releaseLock();
  }
}

function migrateSeenIds_(root) {
  var props = PropertiesService.getScriptProperties();
  var raw = props.getProperty(CONFIG.MSG_ID_PROP);
  if (raw == null) return;
  var old = JSON.parse(raw);
  var seen = loadSeenIds_(root);
  old.forEach(function (id) {
    if (!seen.ids[id]) { seen.ids[id] = true; seen.order.push(id); }
  });
  saveSeenIds_(seen, root);
  var check = loadSeenIds_(root).ids;
  var lost = old.filter(function (id) { return !check[id]; });
  if (lost.length) throw new Error('Migration kept the property: ' + lost.length + ' ID(s) missing from ' + CONFIG.SEEN_IDS_FILE);
  props.deleteProperty(CONFIG.MSG_ID_PROP);
  Logger.log('Moved %s processed-message ID(s) from the %s Script Property into %s and deleted the property.',
    old.length, CONFIG.MSG_ID_PROP, CONFIG.SEEN_IDS_FILE);
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

function collectFiles_(folder, out, skipFolderId) {
  var files = folder.getFiles();
  while (files.hasNext()) out.push(files.next());
  var subs = folder.getFolders();
  while (subs.hasNext()) {
    var sub = subs.next();
    if (sub.getId() !== skipFolderId) collectFiles_(sub, out, skipFolderId);
  }
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

/** Distinct http(s) links in a text or HTML body, in order of appearance, at most 20. */
function extractLinks_(text) {
  var out = [], seen = {};
  (String(text || '').match(/https?:\/\/[^\s<>"'`]+/gi) || []).forEach(function (url) {
    url = url.replace(/&amp;/g, '&').replace(/[.,;:!?)\]]+$/, '');
    if (!seen[url] && out.length < 20) { seen[url] = true; out.push(url); }
  });
  return out;
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
