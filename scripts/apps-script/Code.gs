// Google Apps Script Web App: the backend for the app's "Save to GitHub"
// login feature, and for the "Propose" tab's question submissions. It's a
// thin relay in front of the GitHub Contents API — everything it manages
// (a users.json file, one progress file per user, and student-proposed
// questions appended to db/staging/proposed_questions.json) lives in this
// repo as plain JSON, and the GitHub token needed to write there lives only
// in this script's Script Properties. It never reaches the browser.
//
// This is deliberately NOT hardened security. Accounts are a username +
// password compared as a SHA-256 hash (no salt) — enough to stop a random
// visitor from overwriting someone else's save (or spamming proposals
// anonymously), not a real auth system. A proposed question never reaches
// the live database on its own either way — it only ever lands in staging,
// same as one added locally via tools/add_question.html, and still needs a
// human to run scripts/review_staged_questions.py before it's real. This is
// a personal/shared-with-friends tool, not a system worth over-engineering.
//
// One-time setup:
//   1. https://script.google.com/ -> New project. Paste this file in as Code.gs.
//   2. Project Settings (gear icon) -> Script Properties -> add:
//        GITHUB_TOKEN   a GitHub personal access token with Contents
//                       read/write on the target repo (a fine-grained PAT
//                       scoped to just that repo is enough)
//        GITHUB_OWNER   e.g. "kyj9981"
//        GITHUB_REPO    e.g. "kyj-sat"
//        GITHUB_BRANCH  e.g. "main" (optional, defaults to "main")
//        GITHUB_DIR     e.g. "db/cloud_save" (optional, defaults to that —
//                       holds users.json + progress/<username>.json)
//        ADMIN_SECRET   optional, a password only you know — set this if you
//                       want the ability to reset a user's forgotten
//                       password yourself (see handleAdminResetPassword_
//                       below). Passwords are one-way hashed, so this is a
//                       reset, not a recovery — you can never see the
//                       original password, yours or anyone else's.
//   3. Deploy -> New deployment -> type "Web app".
//        Execute as: Me
//        Who has access: Anyone
//      (public access is fine — every write still requires a matching
//      username/password, and the token itself never leaves this script)
//   4. Copy the resulting /exec URL into CLOUD_SAVE_URL near the top of
//      site/index.template.html, then rebuild (see README.md).

function doPost(e) {
  var result;
  try {
    // Apps Script web apps don't answer CORS preflight (OPTIONS) requests,
    // so the app POSTs with Content-Type: text/plain to keep it a "simple
    // request" and avoid triggering one — we still parse the body as JSON.
    var body = JSON.parse(e.postData.contents);
    var action = body.action || 'save';
    if (action === 'signup') result = handleSignup_(body);
    else if (action === 'login') result = handleLogin_(body);
    else if (action === 'save') result = handleSave_(body);
    else if (action === 'load') result = handleLoad_(body);
    else if (action === 'propose_question') result = handlePropose_(body);
    else if (action === 'report_question') result = handleReportQuestion_(body);
    else if (action === 'leaderboard') result = handleLeaderboard_(body);
    else if (action === 'admin_reset_password') result = handleAdminResetPassword_(body);
    else if (action === 'save_blueprint') result = handleSaveBlueprint_(body);
    else if (action === 'load_blueprint') result = handleLoadBlueprint_(body);
    else if (action === 'list_blueprints') result = handleListBlueprints_(body);
    else if (action === 'get_ratings') result = handleGetRatings_(body);
    else if (action === 'submit_difficulty_votes') result = handleSubmitDifficultyVotes_(body);
    else if (action === 'admin_set_rating') result = handleAdminSetRating_(body);
    else result = { ok: false, error: 'Unknown action: ' + action };
  } catch (err) {
    result = { ok: false, error: String((err && err.message) || err) };
  }
  return ContentService.createTextOutput(JSON.stringify(result))
    .setMimeType(ContentService.MimeType.JSON);
}

// ---------- config ----------

function config_() {
  var props = PropertiesService.getScriptProperties();
  var token = props.getProperty('GITHUB_TOKEN');
  var owner = props.getProperty('GITHUB_OWNER');
  var repo = props.getProperty('GITHUB_REPO');
  if (!token || !owner || !repo) {
    throw new Error('Script is missing GITHUB_TOKEN/GITHUB_OWNER/GITHUB_REPO properties');
  }
  return {
    apiRoot: 'https://api.github.com/repos/' + owner + '/' + repo + '/contents',
    branch: props.getProperty('GITHUB_BRANCH') || 'main',
    dir: (props.getProperty('GITHUB_DIR') || 'db/cloud_save').replace(/\/+$/, ''),
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'sat-qbank-cloud-save',
    },
  };
}

function usersPath_(cfg) { return cfg.dir + '/users.json'; }
function progressPath_(cfg, username) { return cfg.dir + '/progress/' + username.toLowerCase() + '.json'; }
// Single small JSON array of every community-shared blueprint (Test/Practice
// setup) — same one-file-holds-a-list shape as users.json, since this is a
// personal/shared-with-friends tool and the list is expected to stay small.
function blueprintsPath_(cfg) { return cfg.dir + '/blueprints.json'; }
// Fixed repo location (not under cfg.dir, which is only the configurable
// cloud-save directory) — matches scripts/review_staged_questions.py.
function stagingPath_() { return 'db/staging/proposed_questions.json'; }
// Fixed repo location, shared across all users (unlike progress files) —
// one small JSON object of community difficulty votes + admin-approved
// ratings, keyed by question id. See handleGetRatings_/mergeDifficultyVotes_.
function ratingsPath_() { return 'db/ratings/aggregate.json'; }
// Same idea as stagingPath_, but for "something's wrong with this question"
// flags from the Report button — a fixed shared file, not a per-user one,
// so the site owner can see every open report in one place.
function reportsPath_() { return 'db/staging/reported_questions.json'; }
// Fun, cosmetic-only titles shown on the leaderboard next to a username
// (e.g. {"hamin": "Average Lebron Fan"}) — hand-edited in the repo, not
// settable by users themselves. Fixed shared location, same idea as
// ratingsPath_/reportsPath_.
function userTitlesPath_() { return 'db/user_titles.json'; }

// ---------- GitHub Contents API helpers ----------

function githubGetFile_(cfg, path) {
  var url = cfg.apiRoot + '/' + path + '?ref=' + encodeURIComponent(cfg.branch);
  var resp = UrlFetchApp.fetch(url, { method: 'get', headers: cfg.headers, muteHttpExceptions: true });
  if (resp.getResponseCode() === 200) {
    var json = JSON.parse(resp.getContentText());
    var content = Utilities.newBlob(Utilities.base64Decode(json.content.replace(/\n/g, ''))).getDataAsString();
    return { exists: true, sha: json.sha, content: content };
  }
  if (resp.getResponseCode() === 404) return { exists: false, sha: null, content: null };
  throw new Error('GitHub GET ' + path + ' failed: ' + resp.getResponseCode() + ' ' + resp.getContentText());
}

function githubPutFile_(cfg, path, contentText, sha, message) {
  var url = cfg.apiRoot + '/' + path;
  var payload = {
    message: message,
    content: Utilities.base64Encode(Utilities.newBlob(contentText).getBytes()),
    branch: cfg.branch,
  };
  if (sha) payload.sha = sha;
  var resp = UrlFetchApp.fetch(url, {
    method: 'put',
    headers: cfg.headers,
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
  if (resp.getResponseCode() !== 200 && resp.getResponseCode() !== 201) {
    throw new Error('GitHub PUT ' + path + ' failed: ' + resp.getResponseCode() + ' ' + resp.getContentText());
  }
  return JSON.parse(resp.getContentText());
}

// Same as githubPutFile_, but for content that's already base64-encoded
// (binary files, e.g. a proposed question's image) — skips the text->bytes
// re-encoding step, which would corrupt binary data.
function githubPutFileBase64_(cfg, path, base64Content, sha, message) {
  var url = cfg.apiRoot + '/' + path;
  var payload = { message: message, content: base64Content, branch: cfg.branch };
  if (sha) payload.sha = sha;
  var resp = UrlFetchApp.fetch(url, {
    method: 'put',
    headers: cfg.headers,
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  });
  if (resp.getResponseCode() !== 200 && resp.getResponseCode() !== 201) {
    throw new Error('GitHub PUT ' + path + ' failed: ' + resp.getResponseCode() + ' ' + resp.getContentText());
  }
  return JSON.parse(resp.getContentText());
}

// ---------- auth ----------

function isValidUsername_(u) {
  return typeof u === 'string' && /^[a-zA-Z0-9_-]{3,32}$/.test(u);
}

function sha256Hex_(text) {
  var digest = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, text, Utilities.Charset.UTF_8);
  return digest.map(function (b) {
    var v = (b < 0 ? b + 256 : b).toString(16);
    return v.length === 1 ? '0' + v : v;
  }).join('');
}

function loadUsers_(cfg) {
  var file = githubGetFile_(cfg, usersPath_(cfg));
  return { sha: file.sha, users: file.exists ? JSON.parse(file.content) : {} };
}

// { ok: true, username } on match, { ok: false, error } otherwise.
function checkCredentials_(cfg, username, password) {
  if (!isValidUsername_(username)) return { ok: false, error: 'Invalid username' };
  if (!password) return { ok: false, error: 'Invalid password' };
  var users = loadUsers_(cfg).users;
  var rec = users[username.toLowerCase()];
  if (!rec || rec.passwordHash !== sha256Hex_(String(password))) {
    return { ok: false, error: 'Wrong username or password' };
  }
  return { ok: true, username: rec.username };
}

// ---------- actions ----------

function handleSignup_(body) {
  var username = body && body.username;
  var password = body && body.password;
  if (!isValidUsername_(username)) {
    return { ok: false, error: 'Username must be 3-32 characters: letters, numbers, - or _' };
  }
  if (!password || String(password).length < 4) {
    return { ok: false, error: 'Password must be at least 4 characters' };
  }
  var cfg = config_();
  var loaded = loadUsers_(cfg);
  var key = username.toLowerCase();
  if (loaded.users[key]) return { ok: false, error: 'That username is taken' };
  loaded.users[key] = {
    username: username,
    passwordHash: sha256Hex_(String(password)),
    createdAt: new Date().toISOString(),
  };
  githubPutFile_(cfg, usersPath_(cfg), JSON.stringify(loaded.users, null, 2), loaded.sha, 'Add user ' + username);
  return { ok: true, username: username };
}

function handleLogin_(body) {
  var cfg = config_();
  return checkCredentials_(cfg, body && body.username, body && body.password);
}

function handleSave_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;
  if (!body.data) return { ok: false, error: 'Missing data' };

  var path = progressPath_(cfg, check.username);
  var existing = githubGetFile_(cfg, path);
  var result = githubPutFile_(
    cfg, path,
    JSON.stringify(body.data, null, 2),
    existing.sha,
    body.message || ('Update ' + check.username + ' progress — ' + new Date().toISOString())
  );
  return {
    ok: true,
    commitSha: result.commit && result.commit.sha,
    commitUrl: result.commit && result.commit.html_url,
  };
}

function handleLoad_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var file = githubGetFile_(cfg, progressPath_(cfg, check.username));
  if (!file.exists) return { ok: true, data: null };
  return { ok: true, data: JSON.parse(file.content) };
}

// Any logged-in user can see everyone else's streak/activity — a small
// "who's grinding" board for a group of friends, not a private stat. Only
// the fields the leaderboard needs are returned (completionLog, dailyGoal,
// display name, mock-exam count, total-done) — never passwordHash, and the
// client derives streaks from completionLog itself (same computeStreaks()
// used for the requester's own Profile tab) so the two never drift apart.
//
// totalDone is counted here from `progress` (status === 'done') rather than
// sent as the raw progress map (which would mean shipping one entry per
// question in the whole bank) — completionLog only covers questions
// completed *after* that log was introduced, so it undercounts anyone with
// older progress; `progress` is the source of truth the app itself uses for
// "questions done" (see computeGamificationStats's totalDone client-side).
function loadUserTitles_(cfg) {
  var file = githubGetFile_(cfg, userTitlesPath_());
  if (!file.exists) return {};
  try { return JSON.parse(file.content) || {}; } catch (e) { return {}; }
}

function handleLeaderboard_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var titles = loadUserTitles_(cfg);
  var users = loadUsers_(cfg).users;
  var entries = [];
  Object.keys(users).forEach(function (key) {
    var rec = users[key];
    var file = githubGetFile_(cfg, progressPath_(cfg, rec.username));
    if (!file.exists) return;
    var data;
    try { data = JSON.parse(file.content); } catch (e) { return; }
    var progress = data.progress || {};
    var totalDone = Object.keys(progress).filter(function (qid) { return progress[qid] === 'done'; }).length;
    entries.push({
      username: rec.username,
      name: (data.profile && data.profile.name) || rec.username,
      title: titles[rec.username] || titles[rec.username.toLowerCase()] || null,
      completionLog: data.completionLog || {},
      dailyGoal: data.dailyGoal || 10,
      mockCount: (data.mockHistory || []).length,
      totalDone: totalDone,
    });
  });
  return { ok: true, entries: entries };
}

// Lets the site owner reset a user's password without knowing the old one
// (passwords are stored as a one-way hash — see checkCredentials_ — so
// there's no way to recover the original). Not exposed in any UI; call it
// directly, e.g. from the Apps Script editor's "Run" or via curl, with an
// ADMIN_SECRET set in Script Properties:
//   curl -X POST '<exec url>' -H 'Content-Type: text/plain' -d \
//     '{"action":"admin_reset_password","adminSecret":"...","username":"someone","newPassword":"..."}'
function handleAdminResetPassword_(body) {
  var props = PropertiesService.getScriptProperties();
  var adminSecret = props.getProperty('ADMIN_SECRET');
  if (!adminSecret) return { ok: false, error: 'ADMIN_SECRET is not set in Script Properties' };
  if (!body || body.adminSecret !== adminSecret) return { ok: false, error: 'Not authorized' };

  var username = body.username;
  var newPassword = body.newPassword;
  if (!isValidUsername_(username)) return { ok: false, error: 'Invalid username' };
  if (!newPassword || String(newPassword).length < 4) {
    return { ok: false, error: 'New password must be at least 4 characters' };
  }

  var cfg = config_();
  var loaded = loadUsers_(cfg);
  var key = username.toLowerCase();
  var rec = loaded.users[key];
  if (!rec) return { ok: false, error: 'No such user' };

  rec.passwordHash = sha256Hex_(String(newPassword));
  githubPutFile_(cfg, usersPath_(cfg), JSON.stringify(loaded.users, null, 2), loaded.sha, 'Admin reset password for ' + rec.username);
  return { ok: true, username: rec.username };
}

// A student-submitted question (from the site's "Propose" tab). This only
// ever appends to the staging file — the exact same
// db/staging/proposed_questions.json that tools/add_question.html writes to
// locally — never db/categories/*.json directly. The site owner still has
// to pull the repo and run scripts/review_staged_questions.py to actually
// approve or reject it; this endpoint has no authority to add a question to
// the live bank by itself.
function handlePropose_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var record = body && body.record;
  if (!record || typeof record !== 'object' || !record.id || !record.category) {
    return { ok: false, error: 'Missing or malformed record' };
  }
  record.proposed_by = check.username;
  record.proposed_at = new Date().toISOString();

  var path = stagingPath_();
  var result, count, lastErr;
  // A GitHub Contents API PUT needs the current file sha, so two students
  // submitting close together can race (the second one's sha goes stale
  // between its GET and PUT). Retry a few times with a fresh GET/sha rather
  // than surfacing that as a submit failure for ordinary concurrent use.
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var existing = githubGetFile_(cfg, path);
      var staged = existing.exists ? JSON.parse(existing.content) : [];
      staged.push(record);
      count = staged.length;
      result = githubPutFile_(
        cfg, path, JSON.stringify(staged, null, 2), existing.sha,
        'Propose question ' + record.id + ' (by ' + check.username + ')'
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result) throw lastErr;

  if (body.image_base64 && body.image_rel_path) {
    var imgExisting = githubGetFile_(cfg, body.image_rel_path);
    githubPutFileBase64_(
      cfg, body.image_rel_path, body.image_base64, imgExisting.sha,
      'Add proposed image for ' + record.id
    );
  }

  return { ok: true, count: count, commitUrl: result.commit && result.commit.html_url };
}

// A batch of "report a problem with this question" flags from one client.
// The client queues these locally (see reportQuestion() in
// site/index.template.html) and only ever sends them along with the next
// manual "Save progress now" click — never immediately — so this can arrive
// with several reports built up in one call. Appends to the same shared
// staging file every client writes to; nothing here reaches the live
// question bank on its own, same guarantee as handlePropose_ below.
function handleReportQuestion_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var reports = body && body.reports;
  if (!Array.isArray(reports) || !reports.length) return { ok: false, error: 'Missing or empty reports' };
  var records = [];
  for (var i = 0; i < reports.length; i++) {
    var r = reports[i];
    if (!r || typeof r !== 'object' || !r.qid || !r.reason) continue;
    records.push({
      qid: String(r.qid),
      shortId: r.shortId ? String(r.shortId) : String(r.qid),
      reason: String(r.reason).slice(0, 100),
      note: r.note ? String(r.note).slice(0, 500) : '',
      reported_by: check.username,
      reported_at: new Date().toISOString(),
    });
  }
  if (!records.length) return { ok: false, error: 'No valid reports in batch' };

  var path = reportsPath_();
  var result, count, lastErr;
  // Same GitHub-sha race as handlePropose_ (two people saving at once) —
  // retry with a fresh GET/sha rather than surfacing that as a failure.
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var existing = githubGetFile_(cfg, path);
      var staged = existing.exists ? JSON.parse(existing.content) : [];
      staged = staged.concat(records);
      count = staged.length;
      result = githubPutFile_(
        cfg, path, JSON.stringify(staged, null, 2), existing.sha,
        'Report ' + records.length + ' question issue(s) (by ' + check.username + ')'
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result) throw lastErr;

  return { ok: true, count: count, commitUrl: result.commit && result.commit.html_url };
}

// ---------- Blueprints (share custom Test/Practice setups via a short code) ----------
// Same "public reads, credentialed writes" shape as propose_question — a
// share code is just an index into db/cloud_save/blueprints.json, a single
// JSON array. Anyone can list/load (it's meant to be discovered), but
// publishing one requires a kyj-cloud login, same basic spam guard as
// proposing a question.

function randomCode_() {
  var chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; // no 0/O/1/I — easier to read aloud/type
  var out = '';
  for (var i = 0; i < 6; i++) out += chars.charAt(Math.floor(Math.random() * chars.length));
  return out;
}

function loadBlueprints_(cfg) {
  var file = githubGetFile_(cfg, blueprintsPath_(cfg));
  var list = [];
  if (file.exists) {
    try { list = JSON.parse(file.content); } catch (e) { list = []; }
    if (!Array.isArray(list)) list = [];
  }
  return { sha: file.sha, list: list };
}

function handleSaveBlueprint_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var blueprint = body && body.blueprint;
  if (!blueprint || typeof blueprint !== 'object' || !blueprint.name || !blueprint.view) {
    return { ok: false, error: 'Missing or malformed blueprint' };
  }

  var result, code, lastErr;
  // Same optimistic-concurrency retry pattern as handlePropose_ — two people
  // sharing at once would otherwise race on blueprints.json's sha.
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var loaded = loadBlueprints_(cfg);
      var existingCodes = {};
      loaded.list.forEach(function (b) { existingCodes[b.code] = true; });
      do { code = randomCode_(); } while (existingCodes[code]);

      var entry = {
        code: code,
        name: String(blueprint.name).slice(0, 80),
        view: blueprint.view,
        author: check.username,
        createdAt: new Date().toISOString(),
        data: blueprint,
      };
      loaded.list.push(entry);
      result = githubPutFile_(
        cfg, blueprintsPath_(cfg), JSON.stringify(loaded.list, null, 2), loaded.sha,
        'Share blueprint "' + entry.name + '" (' + code + ') by ' + check.username
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result) throw lastErr;
  return { ok: true, code: code };
}

function handleLoadBlueprint_(body) {
  var cfg = config_();
  var code = body && String(body.code || '').trim().toUpperCase();
  if (!code) return { ok: false, error: 'Missing code' };
  var list = loadBlueprints_(cfg).list;
  var entry = list.filter(function (b) { return b.code === code; })[0];
  if (!entry) return { ok: false, error: 'No blueprint found for code ' + code };
  return { ok: true, entry: entry };
}

// No login required — this is the "browse what others made" list, meant to
// be public the same way the blueprints themselves are once shared.
function handleListBlueprints_(body) {
  var cfg = config_();
  var list = loadBlueprints_(cfg).list;
  // Newest first; the raw `data` blob isn't needed until something is
  // actually picked, so keep the listing itself light.
  var entries = list.map(function (b) {
    return { code: b.code, name: b.name, view: b.view, author: b.author, createdAt: b.createdAt };
  }).reverse();
  return { ok: true, entries: entries };
}

// ---------- Rated Difficulty (Lab) ----------
// A GD-style 1-10 community difficulty rating, additive to the existing
// static difficulty/difficulty_label on each question. Same "queue locally,
// flush only on manual save" convention as reportQuestion_/handleReportQuestion_
// above — the client queues votes locally and sends them all in one
// 'submit_difficulty_votes' call alongside the next "Save progress now"
// click, never per-vote. Shape of db/ratings/aggregate.json:
//   { "<questionId>": { votes: { "<username>": 7 }, officialRating: null|1-10, status: "unrated"|"pending"|"approved" } }

function loadRatings_(cfg) {
  var file = githubGetFile_(cfg, ratingsPath_());
  var data = {};
  if (file.exists) {
    try { data = JSON.parse(file.content); } catch (e) { data = {}; }
    if (!data || typeof data !== 'object') data = {};
  }
  return { sha: file.sha, data: data };
}

// Folds one user's votes ({questionId: 1-10, ...}) into the shared ratings
// file. Keyed by username so re-voting/re-saving overwrites cleanly instead
// of double-counting.
function mergeDifficultyVotes_(cfg, username, votes) {
  var lastErr;
  for (var attempt = 0; attempt < 3; attempt++) {
    try {
      var loaded = loadRatings_(cfg);
      var data = loaded.data;
      Object.keys(votes).forEach(function (qid) {
        var rating = Number(votes[qid]);
        // 0 is the client's "I un-voted this" sentinel (see clearDifficultyVote
        // in site/index.template.html) — remove any existing vote from this
        // user rather than storing it. Anything else outside 1-10 is invalid,
        // not a real vote either way.
        if (rating === 0) {
          if (data[qid] && data[qid].votes) delete data[qid].votes[username];
          if (data[qid] && !Object.keys(data[qid].votes).length && data[qid].status === 'pending') {
            data[qid].status = 'unrated';
          }
          return;
        }
        if (!(rating >= 1 && rating <= 10)) return;
        if (!data[qid]) data[qid] = { votes: {}, officialRating: null, status: 'unrated' };
        data[qid].votes[username] = rating;
        if (data[qid].status === 'unrated') data[qid].status = 'pending';
      });
      githubPutFile_(
        cfg, ratingsPath_(), JSON.stringify(data, null, 2), loaded.sha,
        'Difficulty votes from ' + username
      );
      return;
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  throw lastErr;
}

// Public, no login required — the ratings file only ever holds question ids
// and small numbers, same "safe to expose" reasoning as handleListBlueprints_.
function handleGetRatings_(body) {
  var cfg = config_();
  return { ok: true, ratings: loadRatings_(cfg).data };
}

function handleSubmitDifficultyVotes_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var votes = body && body.votes;
  if (!votes || typeof votes !== 'object' || Array.isArray(votes) || !Object.keys(votes).length) {
    return { ok: false, error: 'Missing or empty votes' };
  }
  mergeDifficultyVotes_(cfg, check.username, votes);
  return { ok: true, count: Object.keys(votes).length };
}

// Site-owner-only, same shared-secret pattern as handleAdminResetPassword_.
// Not exposed in any UI; call it directly via curl:
//   curl -X POST '<exec url>' -H 'Content-Type: text/plain' -d \
//     '{"action":"admin_set_rating","adminSecret":"...","questionId":"2d1e5eff","officialRating":7}'
function handleAdminSetRating_(body) {
  var props = PropertiesService.getScriptProperties();
  var adminSecret = props.getProperty('ADMIN_SECRET');
  if (!adminSecret) return { ok: false, error: 'ADMIN_SECRET is not set in Script Properties' };
  if (!body || body.adminSecret !== adminSecret) return { ok: false, error: 'Not authorized' };

  var questionId = body.questionId;
  var officialRating = Number(body.officialRating);
  if (!questionId || typeof questionId !== 'string') return { ok: false, error: 'Missing questionId' };
  if (!(officialRating >= 0 && officialRating <= 10)) {
    return { ok: false, error: 'officialRating must be 0-10' };
  }

  var cfg = config_();
  var lastErr;
  for (var attempt = 0; attempt < 3; attempt++) {
    try {
      var loaded = loadRatings_(cfg);
      var data = loaded.data;
      if (!data[questionId]) data[questionId] = { votes: {}, officialRating: null, status: 'unrated' };
      data[questionId].officialRating = officialRating;
      data[questionId].status = 'approved';
      githubPutFile_(
        cfg, ratingsPath_(), JSON.stringify(data, null, 2), loaded.sha,
        'Admin set official rating for ' + questionId + ' = ' + officialRating
      );
      return { ok: true, questionId: questionId, officialRating: officialRating };
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  throw lastErr;
}
