#!/usr/bin/env node
// validate_registry.js: validates stores_registry.json before it is uploaded for the agent.
//   node tools/validate_registry.js sample-data/seed/stores_registry.json
//
// Catches the mistakes that would silently corrupt a run: duplicate IDs, stores that
// can't satisfy the two-field identity rule (their documents would all quarantine),
// active stores missing what analysis needs, and inconsistent accountant settings.

const fs = require("fs");

const path = process.argv[2] || "stores_registry.json";
const problems = [];
const warnings = [];

// 1. File exists and is valid JSON
let reg;
try {
  reg = JSON.parse(fs.readFileSync(path, "utf8"));
} catch (e) {
  console.error(`FAIL: ${path} is not valid JSON.\n  ${e.message}`);
  console.error(
    "  Tip: the position in the message is a character offset; a missing comma or quote is usually just before it."
  );
  process.exit(1);
}

const meta = reg.meta || {};
const stores = reg.stores || [];
if (meta.expected_store_count && stores.length !== meta.expected_store_count)
  warnings.push(`Expected ${meta.expected_store_count} store blocks, found ${stores.length}.`);

// 2. Per-store checks
const seenIds = new Set();
const seenCodes = new Map();
const senders = new Set();
const allBankAccounts = new Map(); // bank name -> [store_ids] (shared names weaken identity)

for (const s of stores) {
  const id = s.store_id || "(missing store_id)";

  if (!/^S\d{2}$/.test(id)) problems.push(`${id}: store_id must look like S07.`);
  if (seenIds.has(id)) problems.push(`${id}: duplicate store_id.`);
  seenIds.add(id);

  if (!["active", "filed-only"].includes(s.status))
    problems.push(`${id}: status is "${s.status}"; must be "active" or "filed-only".`);
  if (s.status === "active" && !["active", "light"].includes(s.tier))
    problems.push(`${id}: tier is "${s.tier}"; active stores need tier "active" or "light".`);

  // bank_accounts: list of bank-name strings (one BR expected per entry)
  if (!Array.isArray(s.bank_accounts))
    problems.push(`${id}: bank_accounts must be a LIST (use [] when unknown), got ${typeof s.bank_accounts}.`);
  else {
    for (const b of s.bank_accounts) {
      if (typeof b !== "string" || b.trim() === "")
        problems.push(`${id}: bank_accounts contains an empty/non-string entry.`);
      else {
        const key = b.trim().toUpperCase();
        if (!allBankAccounts.has(key)) allBankAccounts.set(key, []);
        allBankAccounts.get(key).push(id);
      }
    }
    if (new Set(s.bank_accounts.map((b) => String(b).trim().toUpperCase())).size !== s.bank_accounts.length)
      problems.push(`${id}: duplicate bank name within bank_accounts.`);
  }

  // store_code: the brand's store number
  if (s.store_code && !/^\d{3,5}$/.test(String(s.store_code)))
    warnings.push(`${id}: store_code "${s.store_code}" doesn't look like a store number.`);
  if (s.store_code) {
    if (seenCodes.has(String(s.store_code)))
      problems.push(`${id}: store_code ${s.store_code} already used by ${seenCodes.get(String(s.store_code))}.`);
    seenCodes.set(String(s.store_code), id);
  }

  // two-field identity rule: legal_entity / store_code / address_fragment / bank_accounts (non-empty)
  const identity = [
    s.legal_entity,
    s.store_code,
    s.address_fragment,
    Array.isArray(s.bank_accounts) && s.bank_accounts.length > 0 ? "banks" : "",
  ].filter((v) => v && String(v).trim() !== "");
  if (identity.length < 2)
    problems.push(
      `${id}: only ${identity.length} identity field(s) filled ` +
        `(legal_entity / store_code / address_fragment / bank_accounts); needs ≥2 or its documents will quarantine.`
    );

  // accountant sender: one accountant, one address
  const sender = s.accountant && s.accountant.email_sender;
  if (!sender || /TBD/i.test(sender)) problems.push(`${id}: accountant.email_sender still TBD/empty.`);
  else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(sender))
    problems.push(`${id}: accountant.email_sender doesn't look like an email: "${sender}"`);
  else senders.add(sender.toLowerCase());

  // active stores need what analysis needs
  if (s.status === "active") {
    if (!Array.isArray(s.bank_accounts) || s.bank_accounts.length === 0)
      problems.push(`${id}: active but bank_accounts is empty; the completeness matrix can't know how many BRs to expect.`);
    if (!s.baseline_file) problems.push(`${id}: active but no baseline_file.`);
  }

  if (s.baseline_file && s.baseline_file !== `baselines/${id}_baseline.json`)
    warnings.push(`${id}: baseline_file is "${s.baseline_file}"; expected "baselines/${id}_baseline.json".`);
}

// 3. One accountant → one sender everywhere
if (senders.size > 1)
  problems.push(`email_sender differs across stores: ${[...senders].join(", ")}; one accountant means one value.`);

// 4. A bank name shared by multiple stores can't distinguish them
for (const [bankName, ids] of allBankAccounts)
  if (ids.length > 1)
    warnings.push(`bank "${bankName}" appears on ${ids.join(", ")}; shared names are weak identity evidence, so those stores need another field filled.`);

// 5. Pilot cap on active stores
const active = stores.filter((s) => s.status === "active").map((s) => s.store_id);
if (meta.pilot_max_active && active.length > meta.pilot_max_active)
  problems.push(`${active.length} active stores (${active.join(", ")}); pilot caps at ${meta.pilot_max_active}.`);

// 6. Report
const brTotal = stores.reduce((n, s) => n + (Array.isArray(s.bank_accounts) ? s.bank_accounts.length : 0), 0);
for (const w of warnings) console.log(`  warn: ${w}`);
if (problems.length) {
  console.log(`\nFAIL: ${problems.length} problem(s):`);
  for (const p of problems) console.log(`  ✗ ${p}`);
  process.exit(1);
} else {
  console.log(
    `\nOK: ${stores.length} stores, ${active.length} active, ${brTotal} bank account(s) listed ` +
      `(the completeness matrix expects that many BRs per month). Safe to upload.`
  );
}
