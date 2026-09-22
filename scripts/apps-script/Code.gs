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
//        PROGRESS_BRANCH  e.g. "cloud-data" (optional, defaults to that —
//                       every progress save/load goes here instead of
//                       GITHUB_BRANCH, so per-user saves — hourly-ish with
//                       Lab auto-sync on — don't clutter main's commit log.
//                       Auto-created on first save by forking GITHUB_BRANCH's
//                       current HEAD; nothing to set up by hand)
//        ADMIN_SECRET   optional, a password only you know — set this if you
//                       want the ability to reset a user's forgotten
//                       password yourself (see handleAdminResetPassword_
//                       below), or to list usernames (handleAdminListUsers_).
//                       Passwords are one-way hashed, so this is a reset, not
//                       a recovery — you can never see the original
//                       password, yours or anyone else's. This is also the
//                       secret scripts/dashboard_server.py needs in its own
//                       (untracked) db/staging/dashboard_config.json to
//                       drive the "Users & Passwords" tab of tools/dashboard.html.
//        ADMIN_EMAIL    optional, where sendAdminDigestEmail_ sends its
//                       summary (defaults to yongjoon9981@gmail.com) — see
//                       "Admin digest email" setup below.
//   3. Deploy -> New deployment -> type "Web app".
//        Execute as: Me
//        Who has access: Anyone
//      (public access is fine — every write still requires a matching
//      username/password, and the token itself never leaves this script)
//   4. Copy the resulting /exec URL into CLOUD_SAVE_URL near the top of
//      site/index.template.html, then rebuild (see README.md). Also put it
//      in db/staging/dashboard_config.json's cloud_save_url (see below).
//
// Admin digest email (optional): once deployed, open this same Apps Script
// project, pick installAdminDigestTrigger_ from the function dropdown, and
// click Run once (authorize Gmail access when asked). That schedules
// sendAdminDigestEmail_ to check every 30 minutes for new student question
// reports, site feedback, and proposed questions, and emails a summary to
// ADMIN_EMAIL/yongjoon9981@gmail.com whenever there's something new — this
// runs on Google's servers, so it keeps working even when your computer is
// off. See the "Admin digest email" comment further down for details.

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
    else if (action === 'get_jumpscare_status') result = handleGetJumpscareStatus_(body);
    else if (action === 'admin_reset_password') result = handleAdminResetPassword_(body);
    else if (action === 'admin_list_users') result = handleAdminListUsers_(body);
    else if (action === 'save_blueprint') result = handleSaveBlueprint_(body);
    else if (action === 'load_blueprint') result = handleLoadBlueprint_(body);
    else if (action === 'list_blueprints') result = handleListBlueprints_(body);
    else if (action === 'delete_blueprint') result = handleDeleteBlueprint_(body);
    else if (action === 'like_blueprint') result = handleLikeBlueprint_(body);
    else if (action === 'get_ratings') result = handleGetRatings_(body);
    else if (action === 'submit_difficulty_votes') result = handleSubmitDifficultyVotes_(body);
    else if (action === 'admin_set_rating') result = handleAdminSetRating_(body);
    else if (action === 'submit_testimonial') result = handleSubmitTestimonial_(body);
    else if (action === 'list_testimonials') result = handleListTestimonials_(body);
    else if (action === 'submit_site_feedback') result = handleSiteFeedback_(body);
    else if (action === 'add_time_record') result = handleAddTimeRecord_(body);
    else if (action === 'get_competition') result = handleGetCompetition_(body);
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
// Small per-user file written alongside the full progress save, holding
// only the handful of fields the Leaderboard needs — see handleLeaderboard_.
// Reading these instead of everyone's full progress blob is what makes the
// leaderboard fast and (structurally, not just by convention) unable to go
// stale the way reading progress from the wrong branch used to.
function leaderboardSummaryPath_(cfg, username) { return cfg.dir + '/leaderboard/' + username.toLowerCase() + '.json'; }
// Shared by handleSave_ (writes it) and handleLeaderboard_'s no-summary-yet
// fallback (derives one on the fly from a full progress file so first-time
// reads before this ever ran are still correct, just slower).
function leaderboardSummaryFor_(username, data) {
  var progress = (data && data.progress) || {};
  var totalDone = Object.keys(progress).filter(function (qid) { return progress[qid] === 'done'; }).length;
  return {
    username: username,
    name: (data && data.profile && data.profile.name) || username,
    optOut: !!(data && data.profile && data.profile.leaderboardOptOut),
    completionLog: (data && data.completionLog) || {},
    dailyGoal: (data && data.dailyGoal) || 10,
    mockCount: ((data && data.mockHistory) || []).length,
    totalDone: totalDone,
  };
}
// Per-user progress saves happen far more often than anything else this
// script writes (every manual "Save progress now" click, and — with the
// Lab auto-sync feature — potentially every couple minutes during active
// use), which was flooding `main`'s commit log with nothing-to-read-later
// noise. They go to their own branch instead: PROGRESS_BRANCH (optional
// Script Property, defaults to "cloud-data") is auto-created on first
// write (see githubEnsureBranch_) by forking main's current HEAD, so it
// starts out with every existing user's progress already in it — no manual
// migration step. Nothing else (build scripts, review tools) ever reads
// this branch, so main stays exactly as clean as before regardless of sync
// frequency.
function progressBranch_(cfg) {
  return PropertiesService.getScriptProperties().getProperty('PROGRESS_BRANCH') || 'cloud-data';
}
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
// Same idea, same fixed shared location — who the mock-exam jumpscare
// easter egg (site/index.template.html's showJumpscare()) fires for. Shape:
// {"enabled": true, "users": ["hamin"]} — "enabled" is a kill switch that
// doesn't require clearing the user list, "users" is lowercase usernames.
// Edited from tools/manage_jumpscare.html (scripts/dashboard_server.py),
// same "edit locally, commit + push to reach the live site" flow as
// user_titles.json — see manage_titles.py's header comment for why.
function jumpscareConfigPath_() { return 'db/jumpscare_config.json'; }
// Home-tab testimonials: submissions land in the staging file (same
// never-live-on-its-own guarantee as proposed questions/reports) until
// scripts/review_testimonials.py approves one into the public file, which
// every visitor's Home tab reads via handleListTestimonials_ below — no
// site rebuild needed to show a newly-approved one.
function testimonialsStagingPath_() { return 'db/staging/testimonials.json'; }
function testimonialsPath_() { return 'db/testimonials.json'; }
// Shared file, not per-user (only two accounts ever use it) — a "Competition"
// tab where the two of you log time spent on things and compete monthly
// (calendar month) and yearly (Jan 1 - Dec 30) on whoever logs LESS total
// time. Kept in one file, keyed by username, so the aggregate math below
// can compare both sides without a second round trip. See handleGetCompetition_
// for why this is safe to keep in one file despite each side not being able
// to see the other's raw numbers.
function competitionPath_() { return 'db/competition/time_records.json'; }
function COMPETITION_USERS_() { return ['hamin', 'kyjv9981']; }

// General "something's wrong with the app" / "you should add X" reports from
// the topbar feedback popover — a separate stream from reportsPath_ above,
// which is specifically "something's wrong with THIS question". No login
// required to submit (see handleSiteFeedback_): the point is to lower
// friction versus the old email-only flow, not to gate it the way a
// public-facing testimonial needs to be.
function siteFeedbackPath_() { return 'db/staging/site_feedback.json'; }

// ---------- GitHub Contents API helpers ----------

function githubGetFile_(cfg, path, branch) {
  var url = cfg.apiRoot + '/' + path + '?ref=' + encodeURIComponent(branch || cfg.branch);
  var resp = UrlFetchApp.fetch(url, { method: 'get', headers: cfg.headers, muteHttpExceptions: true });
  if (resp.getResponseCode() === 200) {
    var json = JSON.parse(resp.getContentText());
    var content = Utilities.newBlob(Utilities.base64Decode(json.content.replace(/\n/g, ''))).getDataAsString();
    return { exists: true, sha: json.sha, content: content };
  }
  if (resp.getResponseCode() === 404) return { exists: false, sha: null, content: null };
  throw new Error('GitHub GET ' + path + ' failed: ' + resp.getResponseCode() + ' ' + resp.getContentText());
}

function githubPutFile_(cfg, path, contentText, sha, message, branch) {
  var url = cfg.apiRoot + '/' + path;
  var payload = {
    message: message,
    content: Utilities.base64Encode(Utilities.newBlob(contentText).getBytes()),
    branch: branch || cfg.branch,
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
function githubPutFileBase64_(cfg, path, base64Content, sha, message, branch) {
  var url = cfg.apiRoot + '/' + path;
  var payload = { message: message, content: base64Content, branch: branch || cfg.branch };
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

// Creates `branch` by forking it off cfg.branch's current HEAD, if it
// doesn't already exist. Idempotent (a 404 on the ref check is the normal
// "not created yet" case, anything else just returns). This is what lets
// PROGRESS_BRANCH come into existence on its own on the very first
// auto-sync/save — no manual "create this branch first" step for whoever
// deploys the script.
//
// Once a branch exists it never stops existing, so the ref-check GET below
// is wasted latency on every single save after the first — one more
// sequential GitHub round-trip inside a doPost call that's already several
// deep (checkCredentials_ + this + the pre-existing-file GET + the PUT),
// which widens the window for the exec->echo redirect hop to flake (see
// cloudSaveRequest's retry logic client-side). CacheService remembers a
// confirmed-existing branch for the rest of the day so most saves skip
// straight past this — a cold cache (new day, new script version, or the
// 6hr GAS cache ceiling) just re-checks once and refills it, same as before.
function githubEnsureBranch_(cfg, branch) {
  var cache = CacheService.getScriptCache();
  var cacheKey = 'branch-exists:' + branch;
  if (cache.get(cacheKey)) return;

  var refUrl = cfg.apiRoot.replace('/contents', '/git/ref/heads/') + encodeURIComponent(branch);
  var refResp = UrlFetchApp.fetch(refUrl, { method: 'get', headers: cfg.headers, muteHttpExceptions: true });
  if (refResp.getResponseCode() === 200) {
    cache.put(cacheKey, '1', 21600); // 6hr — CacheService's own max
    return;
  }

  var baseUrl = cfg.apiRoot.replace('/contents', '/git/ref/heads/') + encodeURIComponent(cfg.branch);
  var baseResp = UrlFetchApp.fetch(baseUrl, { method: 'get', headers: cfg.headers, muteHttpExceptions: true });
  if (baseResp.getResponseCode() !== 200) {
    throw new Error('Could not read base branch "' + cfg.branch + '" to fork ' + branch + ' from: ' + baseResp.getResponseCode());
  }
  var baseSha = JSON.parse(baseResp.getContentText()).object.sha;

  var createUrl = cfg.apiRoot.replace('/contents', '/git/refs');
  var createResp = UrlFetchApp.fetch(createUrl, {
    method: 'post',
    headers: cfg.headers,
    contentType: 'application/json',
    payload: JSON.stringify({ ref: 'refs/heads/' + branch, sha: baseSha }),
    muteHttpExceptions: true,
  });
  // 201 = created; 422 = another concurrent request just created it first — both fine.
  if (createResp.getResponseCode() !== 201 && createResp.getResponseCode() !== 422) {
    throw new Error('Could not create branch ' + branch + ': ' + createResp.getResponseCode() + ' ' + createResp.getContentText());
  }
  cache.put(cacheKey, '1', 21600);
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

  var branch = progressBranch_(cfg);
  githubEnsureBranch_(cfg, branch);
  var path = progressPath_(cfg, check.username);
  var existing = githubGetFile_(cfg, path, branch);
  var result = githubPutFile_(
    cfg, path,
    JSON.stringify(body.data, null, 2),
    existing.sha,
    body.message || ('Update ' + check.username + ' progress — ' + new Date().toISOString()),
    branch
  );
  // Best-effort: keep the leaderboard's small summary file in step with
  // every real save, so handleLeaderboard_ almost never needs its slow
  // full-file fallback — but never let this fail the save itself.
  try {
    var summaryPath = leaderboardSummaryPath_(cfg, check.username);
    var existingSummary = githubGetFile_(cfg, summaryPath, branch);
    githubPutFile_(
      cfg, summaryPath,
      JSON.stringify(leaderboardSummaryFor_(check.username, body.data), null, 2),
      existingSummary.sha,
      'Update ' + check.username + ' leaderboard summary',
      branch
    );
  } catch (e) { /* leaderboard summary is best-effort */ }
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

  var path = progressPath_(cfg, check.username);
  // Progress saves live on their own branch (see progressBranch_) so they
  // don't clutter main's commit log — but anyone who saved before that
  // branch existed still has their only copy sitting on main, so fall back
  // there if the progress branch doesn't have this user yet. Once they
  // save again it lands on the progress branch like everyone else's.
  var file = githubGetFile_(cfg, path, progressBranch_(cfg));
  if (!file.exists) file = githubGetFile_(cfg, path, cfg.branch);
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
// db/user_titles.json is a registry of titles, not a per-user map — each
// title is keyed by a slug id and lists every username it's assigned to,
// so one title (e.g. "Grinder") can cover any number of people and
// editing its emoji/color/reason once updates everyone who has it:
//   { "grinder": { "text": "Grinder", "emoji": "🔥", "color": "#dc2626",
//                  "reason": "...", "users": ["hamin", "kyjv9981"] } }
// Managed via scripts/manage_titles.py or tools/manage_titles.html
// (scripts/titles_server.py).
function loadUserTitles_(cfg) {
  var file = githubGetFile_(cfg, userTitlesPath_());
  if (!file.exists) return {};
  try { return JSON.parse(file.content) || {}; } catch (e) { return {}; }
}

// A badge is {type, text, emoji, color, reason} — emoji + solid color pill,
// reason shown on hover (see .lb-title-badge / leaderboardBadgeHtml_ client
// side). Every badge right now comes from db/user_titles.json (type
// 'custom') — hand-assigned, no computed condition.
// Kill switch: badges are built but not shown yet — flip to true when
// ready to launch them. entries still get a `badges: []` either way, so
// nothing on the client needs to change when this flips.
var LEADERBOARD_BADGES_ENABLED_ = true;

function badgesForUser_(titles, username) {
  var lower = username.toLowerCase();
  var badges = [];
  Object.keys(titles).forEach(function (id) {
    var t = titles[id] || {};
    var users = (t.users || []).map(function (u) { return String(u).toLowerCase(); });
    if (users.indexOf(lower) === -1) return;
    badges.push({
      type: 'custom',
      text: String(t.text || id),
      emoji: t.emoji || '⭐',
      color: t.color || '#1f5f56',
      reason: t.reason || 'Custom title, set by the site owner',
    });
  });
  return badges;
}

function handleLeaderboard_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var titles = loadUserTitles_(cfg);
  var users = loadUsers_(cfg).users;
  var branch = progressBranch_(cfg);

  var entries = [];
  Object.keys(users).forEach(function (key) {
    var rec = users[key];
    var summary = null;
    var summaryFile = githubGetFile_(cfg, leaderboardSummaryPath_(cfg, rec.username), branch);
    if (summaryFile.exists) {
      try { summary = JSON.parse(summaryFile.content); } catch (e) { summary = null; }
    }
    if (!summary) {
      // No summary yet (first read after this was introduced, or their
      // last save predates it) — fall back to a full-file read, same
      // fallback as handleLoad_: real saves live on the progress branch,
      // not main — reading only cfg.branch (main) here is what served
      // stale/absent data for every user whose main snapshot wasn't
      // manually refreshed. Self-heals next time they save.
      var path = progressPath_(cfg, rec.username);
      var file = githubGetFile_(cfg, path, branch);
      if (!file.exists) file = githubGetFile_(cfg, path, cfg.branch);
      if (!file.exists) return;
      var data;
      try { data = JSON.parse(file.content); } catch (e) { return; }
      summary = leaderboardSummaryFor_(rec.username, data);
    }
    if (summary.optOut) return;

    entries.push({
      username: rec.username,
      name: summary.name || rec.username,
      badges: LEADERBOARD_BADGES_ENABLED_ ? badgesForUser_(titles, rec.username) : [],
      completionLog: summary.completionLog || {},
      dailyGoal: summary.dailyGoal || 10,
      mockCount: summary.mockCount || 0,
      totalDone: summary.totalDone || 0,
    });
  });
  return { ok: true, entries: entries };
}

// Whether the mock-exam jumpscare easter egg should fire for this user —
// requires a login same as the leaderboard, since who's on the list isn't
// meant to be publicly enumerable. Reads jumpscareConfigPath_() fresh from
// GitHub every call (no caching) so a toggle from tools/manage_jumpscare.html
// reaches the live site as soon as it's committed + pushed, no rebuild.
function handleGetJumpscareStatus_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var file = githubGetFile_(cfg, jumpscareConfigPath_());
  var conf = { enabled: true, users: [] };
  if (file.exists) {
    try {
      var parsed = JSON.parse(file.content);
      if (parsed && typeof parsed === 'object') conf = parsed;
    } catch (e) { /* keep default */ }
  }
  var users = (conf.users || []).map(function (u) { return String(u).toLowerCase(); });
  var enabled = conf.enabled !== false && users.indexOf(check.username.toLowerCase()) !== -1;
  return { ok: true, enabled: enabled };
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

// Site-owner-only, same shared-secret pattern as handleAdminResetPassword_ —
// lets the local dashboard (scripts/dashboard_server.py -> tools/manage_users.html)
// populate a username picker without ever seeing passwordHash values.
function handleAdminListUsers_(body) {
  var props = PropertiesService.getScriptProperties();
  var adminSecret = props.getProperty('ADMIN_SECRET');
  if (!adminSecret) return { ok: false, error: 'ADMIN_SECRET is not set in Script Properties' };
  if (!body || body.adminSecret !== adminSecret) return { ok: false, error: 'Not authorized' };

  var cfg = config_();
  var users = loadUsers_(cfg).users;
  var list = Object.keys(users).map(function (key) {
    var u = users[key] || {};
    return { username: u.username || key, createdAt: u.createdAt || null };
  }).sort(function (a, b) { return a.username.localeCompare(b.username); });
  return { ok: true, users: list };
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

// A logged-in-only "how KYJ-SAT worked for me" submission for the Home tab —
// same spam guard (a real kyj-cloud account) and same staging-first
// guarantee as handlePropose_/handleReportQuestion_ above.
function handleSubmitTestimonial_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var r = body && body.record;
  if (!r || typeof r !== 'object' || !r.quote) return { ok: false, error: 'Missing testimonial text' };
  var rating = Number(r.rating);
  if (!(rating >= 1 && rating <= 5)) rating = 5;
  var record = {
    rating: rating,
    quote: String(r.quote).slice(0, 500),
    author: r.author ? String(r.author).slice(0, 40) : check.username,
    submitted_by: check.username,
    submitted_at: new Date().toISOString(),
  };

  var path = testimonialsStagingPath_();
  var result, count, lastErr;
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var existing = githubGetFile_(cfg, path);
      var staged = existing.exists ? JSON.parse(existing.content) : [];
      staged.push(record);
      count = staged.length;
      result = githubPutFile_(
        cfg, path, JSON.stringify(staged, null, 2), existing.sha,
        'Submit testimonial (by ' + check.username + ')'
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result) throw lastErr;

  return { ok: true, count: count, commitUrl: result.commit && result.commit.html_url };
}

// Public read (no credentials needed) of whatever's been approved into
// db/testimonials.json — this is what every visitor's Home tab calls.
function handleListTestimonials_(body) {
  var cfg = config_();
  var file = githubGetFile_(cfg, testimonialsPath_());
  var list = [];
  if (file.exists) {
    try { list = JSON.parse(file.content); } catch (e) { list = []; }
    if (!Array.isArray(list)) list = [];
  }
  return { ok: true, testimonials: list };
}

// General app feedback (bug/feature/other) from the topbar popover — no
// credential check, unlike every other write action here, since the whole
// point is a lower-friction alternative to the old mailto link. That does
// mean this file can accumulate spam more easily than the others; it's a
// staging file just like proposed_questions.json, reviewed by a human
// (scripts/review_site_feedback.py) before anything is acted on.
function handleSiteFeedback_(body) {
  var cfg = config_();
  var r = body && body.record;
  if (!r || typeof r !== 'object' || !r.message) return { ok: false, error: 'Missing feedback message' };
  var record = {
    type: ['bug', 'feature', 'other', 'account_recovery'].indexOf(r.type) !== -1 ? r.type : 'other',
    message: String(r.message).slice(0, 1000),
    contact: r.contact ? String(r.contact).slice(0, 80) : '',
    username: r.username ? String(r.username).slice(0, 32) : '',
    page: r.page ? String(r.page).slice(0, 40) : '',
    submitted_at: new Date().toISOString(),
  };

  var path = siteFeedbackPath_();
  var result, count, lastErr;
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var existing = githubGetFile_(cfg, path);
      var staged = existing.exists ? JSON.parse(existing.content) : [];
      staged.push(record);
      count = staged.length;
      result = githubPutFile_(
        cfg, path, JSON.stringify(staged, null, 2), existing.sha,
        'Site feedback: ' + record.type + (record.username ? ' (by ' + record.username + ')' : '')
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
        likes: 0,
        views: 0,
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

// Bumps the view counter as a side effect of resolving a code — turns this
// from a pure read into a read-modify-write, so it needs the same retry
// pattern as the other blueprint handlers below.
function handleLoadBlueprint_(body) {
  var cfg = config_();
  var code = body && String(body.code || '').trim().toUpperCase();
  if (!code) return { ok: false, error: 'Missing code' };
  var result, lastErr, entrySnapshot;
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var loaded = loadBlueprints_(cfg);
      var entry = loaded.list.filter(function (b) { return b.code === code; })[0];
      if (!entry) return { ok: false, error: 'No blueprint found for code ' + code };
      entry.views = (entry.views || 0) + 1;
      entrySnapshot = entry;
      result = githubPutFile_(
        cfg, blueprintsPath_(cfg), JSON.stringify(loaded.list, null, 2), loaded.sha,
        'View blueprint "' + entry.name + '" (' + code + ')'
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result) throw lastErr;
  return { ok: true, entry: entrySnapshot };
}

// Anyone can like (no login required, same tolerance as list/load) — a
// client-side localStorage guard on the liked code prevents casual repeat-
// clicking, not a real per-account abuse control. Same retry pattern as the
// other blueprint handlers.
function handleLikeBlueprint_(body) {
  var cfg = config_();
  var code = body && String(body.code || '').trim().toUpperCase();
  if (!code) return { ok: false, error: 'Missing code' };
  var result, lastErr, newLikes;
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var loaded = loadBlueprints_(cfg);
      var entry = loaded.list.filter(function (b) { return b.code === code; })[0];
      if (!entry) return { ok: false, error: 'No blueprint found for code ' + code };
      entry.likes = (entry.likes || 0) + 1;
      newLikes = entry.likes;
      result = githubPutFile_(
        cfg, blueprintsPath_(cfg), JSON.stringify(loaded.list, null, 2), loaded.sha,
        'Like blueprint "' + entry.name + '" (' + code + ')'
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result) throw lastErr;
  return { ok: true, likes: newLikes };
}

// Retract a previously-shared blueprint. Only the original sharer can do
// this (author must match the logged-in username) — same credential check
// as saving, just gated on ownership afterward. Once removed, the code stops
// resolving for anyone (handleLoadBlueprint_ returns "not found"), same as
// if it had never been shared.
function handleDeleteBlueprint_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;

  var code = body && String(body.code || '').trim().toUpperCase();
  if (!code) return { ok: false, error: 'Missing code' };

  var result, lastErr, removed = false;
  for (var attempt = 0; attempt < 3 && !result; attempt++) {
    try {
      var loaded = loadBlueprints_(cfg);
      var entry = loaded.list.filter(function (b) { return b.code === code; })[0];
      if (!entry) return { ok: false, error: 'No blueprint found for code ' + code };
      if (String(entry.author).toLowerCase() !== String(check.username).toLowerCase()) {
        return { ok: false, error: 'Only ' + entry.author + ' can unshare this blueprint' };
      }
      var next = loaded.list.filter(function (b) { return b.code !== code; });
      removed = true;
      result = githubPutFile_(
        cfg, blueprintsPath_(cfg), JSON.stringify(next, null, 2), loaded.sha,
        'Unshare blueprint "' + entry.name + '" (' + code + ') by ' + check.username
      );
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  if (!result && removed) throw lastErr;
  if (!result) return { ok: false, error: 'No blueprint found for code ' + code };
  return { ok: true };
}

// No login required — this is the "browse what others made" list, meant to
// be public the same way the blueprints themselves are once shared.
function handleListBlueprints_(body) {
  var cfg = config_();
  var list = loadBlueprints_(cfg).list;
  // Newest first; the raw `data` blob isn't needed until something is
  // actually picked, so keep the listing itself light.
  var entries = list.map(function (b) {
    return { code: b.code, name: b.name, view: b.view, author: b.author, createdAt: b.createdAt, likes: b.likes || 0, views: b.views || 0 };
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

// ---------- Competition tab (kyjv9981 vs hamin) ----------
// db/competition/time_records.json shape: { "<username>": [ { id, name,
// description, seconds, createdAt } ] } — createdAt is when the record was
// added (UTC ISO string), which is what buckets it into a month/year below.
// Not a "log what day you did this" field; add-as-you-go is the whole point.

function loadCompetitionData_(cfg) {
  var file = githubGetFile_(cfg, competitionPath_());
  var data = {};
  if (file.exists) {
    try { data = JSON.parse(file.content); } catch (e) { data = {}; }
    if (!data || typeof data !== 'object') data = {};
  }
  return { sha: file.sha, data: data };
}

function requireCompetitionUser_(check) {
  var lower = check.username.toLowerCase();
  if (COMPETITION_USERS_().indexOf(lower) === -1) {
    return { ok: false, error: 'The competition tab is limited to specific accounts' };
  }
  return null;
}

function handleAddTimeRecord_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;
  var deny = requireCompetitionUser_(check);
  if (deny) return deny;

  var name = typeof (body && body.name) === 'string' ? body.name.trim() : '';
  var description = typeof (body && body.description) === 'string' ? body.description.trim() : '';
  var seconds = Math.floor(Number(body && body.seconds));
  if (!name || name.length > 120) return { ok: false, error: 'Name must be 1-120 characters' };
  if (description.length > 500) return { ok: false, error: 'Description is too long' };
  if (!(seconds > 0 && seconds <= 100 * 3600)) return { ok: false, error: 'Duration must be between 1 second and 100 hours' };

  var lower = check.username.toLowerCase();
  var record = {
    id: Utilities.getUuid(),
    name: name,
    description: description,
    seconds: seconds,
    createdAt: new Date().toISOString(),
  };
  var lastErr;
  for (var attempt = 0; attempt < 3; attempt++) {
    try {
      var loaded = loadCompetitionData_(cfg);
      var data = loaded.data;
      if (!Array.isArray(data[lower])) data[lower] = [];
      data[lower].push(record);
      githubPutFile_(
        cfg, competitionPath_(), JSON.stringify(data, null, 2), loaded.sha,
        'Competition: time record from ' + check.username
      );
      return { ok: true, record: record };
    } catch (e) {
      lastErr = e;
      Utilities.sleep(300 * (attempt + 1));
    }
  }
  throw lastErr;
}

function pad2_(n) { return n < 10 ? '0' + n : String(n); }
var COMPETITION_MONTH_NAMES_ = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

// Sums each user's records into per-user seconds totals for a period,
// determined by a predicate on each record's createdAt Date.
function competitionTotalsForPeriod_(data, users, inPeriod) {
  var totals = {};
  users.forEach(function (u) {
    var records = Array.isArray(data[u]) ? data[u] : [];
    totals[u] = records.reduce(function (sum, r) {
      var d = new Date(r.createdAt);
      return inPeriod(d) ? sum + (Number(r.seconds) || 0) : sum;
    }, 0);
  });
  return totals;
}

// { winner: username|null, tie: bool, diffSeconds } — never the raw totals,
// so a closed-period result never leaks how much time either side actually
// logged, only who came out ahead (by logging LESS time) and by how much.
function competitionResultFromTotals_(users, totals) {
  var a = users[0], b = users[1];
  if (totals[a] === totals[b]) return { winner: null, tie: true, diffSeconds: 0 };
  var winner = totals[a] < totals[b] ? a : b;
  return { winner: winner, tie: false, diffSeconds: Math.abs(totals[a] - totals[b]) };
}

function handleGetCompetition_(body) {
  var cfg = config_();
  var check = checkCredentials_(cfg, body && body.username, body && body.password);
  if (!check.ok) return check;
  var deny = requireCompetitionUser_(check);
  if (deny) return deny;

  var users = COMPETITION_USERS_();
  var lower = check.username.toLowerCase();
  var loaded = loadCompetitionData_(cfg);
  var data = loaded.data;
  var ownRecords = (Array.isArray(data[lower]) ? data[lower] : []).slice()
    .sort(function (x, y) { return new Date(y.createdAt) - new Date(x.createdAt); });

  var now = new Date();
  var currentMonthKey = now.getUTCFullYear() + '-' + pad2_(now.getUTCMonth() + 1);
  var currentYear = now.getUTCFullYear();
  // Dec 30 of the current year, end-of-day UTC — the yearly period is
  // Jan 1 - Dec 30 (not Dec 31), per how the competition was defined.
  var currentYearCloses = Date.UTC(currentYear, 11, 31); // first instant AFTER Dec 30

  // Every month/year key either user has at least one record in, so a
  // period both users happened to record zero time in still isn't silently
  // skipped once it's closed (it's a legitimate tie).
  var monthKeys = {}, yearKeys = {};
  users.forEach(function (u) {
    (Array.isArray(data[u]) ? data[u] : []).forEach(function (r) {
      var d = new Date(r.createdAt);
      monthKeys[d.getUTCFullYear() + '-' + pad2_(d.getUTCMonth() + 1)] = true;
      yearKeys[d.getUTCFullYear()] = true;
    });
  });

  var monthlyResults = Object.keys(monthKeys).filter(function (k) { return k < currentMonthKey; })
    .sort().map(function (key) {
      var parts = key.split('-');
      var y = Number(parts[0]), m = Number(parts[1]);
      var totals = competitionTotalsForPeriod_(data, users, function (d) {
        return d.getUTCFullYear() === y && d.getUTCMonth() + 1 === m;
      });
      var result = competitionResultFromTotals_(users, totals);
      result.period = key;
      result.label = COMPETITION_MONTH_NAMES_[m - 1] + ' ' + y;
      return result;
    });

  var yearlyResults = Object.keys(yearKeys).map(Number).filter(function (y) {
    return Date.UTC(y, 11, 31) <= now.getTime();
  }).sort().map(function (y) {
    var totals = competitionTotalsForPeriod_(data, users, function (d) {
      return d.getUTCFullYear() === y && Date.UTC(y, 0, 1) <= d.getTime() && d.getTime() < Date.UTC(y, 11, 31);
    });
    var result = competitionResultFromTotals_(users, totals);
    result.period = String(y);
    result.label = String(y);
    return result;
  });

  return {
    ok: true,
    ownRecords: ownRecords,
    monthlyResults: monthlyResults,
    yearlyResults: yearlyResults,
    currentMonthLabel: COMPETITION_MONTH_NAMES_[now.getUTCMonth()] + ' ' + currentYear,
    currentYearLabel: String(currentYear),
    currentYearClosed: currentYearCloses <= now.getTime(),
  };
}

// ---------- Admin digest email ----------
// Runs on a time-driven trigger (see installAdminDigestTrigger_ below), not
// via doPost — nobody can invoke this over the web. Each feed below is a
// staging file students write to (question reports, general site feedback,
// proposed questions); this walks each one, finds items newer than the
// last run's high-water mark (kept in Script Properties, one property per
// feed, so items that get resolved/removed locally afterward don't cause
// them to be re-reported as "new" next time), and — only if there's
// something actually new — sends one summary email. Silent (no email) on a
// run with nothing new, so this can run every 30 minutes without becoming
// noise.
function adminDigestFeeds_() {
  return [
    {
      key: 'REPORTS', path: reportsPath_(), timeField: 'reported_at', label: 'question report(s)',
      describe: function (r) { return (r.shortId || r.qid) + ' — ' + r.reason + ' (by ' + r.reported_by + ')'; },
    },
    {
      key: 'FEEDBACK', path: siteFeedbackPath_(), timeField: 'submitted_at', label: 'site feedback item(s)',
      describe: function (r) { return '[' + r.type + '] ' + String(r.message || '').slice(0, 90); },
    },
    {
      key: 'PROPOSED', path: stagingPath_(), timeField: 'proposed_at', label: 'proposed question(s)',
      describe: function (r) { return (r.title || r.id) + ' (' + r.category + ', by ' + r.proposed_by + ')'; },
    },
  ];
}

function sendAdminDigestEmail_() {
  var cfg = config_();
  var props = PropertiesService.getScriptProperties();
  var to = props.getProperty('ADMIN_EMAIL') || 'yongjoon9981@gmail.com';

  var sections = [];
  var totalNew = 0;

  adminDigestFeeds_().forEach(function (feed) {
    var propKey = 'DIGEST_LAST_' + feed.key;
    var lastSeen = props.getProperty(propKey) || '';
    var file = githubGetFile_(cfg, feed.path);
    var items = [];
    if (file.exists) {
      try { items = JSON.parse(file.content); } catch (e) { items = []; }
      if (!Array.isArray(items)) items = [];
    }
    var fresh = items.filter(function (it) { return String((it && it[feed.timeField]) || '') > lastSeen; });
    if (!fresh.length) return;
    fresh.sort(function (a, b) { return String(a[feed.timeField]).localeCompare(String(b[feed.timeField])); });

    totalNew += fresh.length;
    var shown = fresh.slice(0, 10).map(function (it) { return '  - ' + feed.describe(it); });
    if (fresh.length > 10) shown.push('  ...and ' + (fresh.length - 10) + ' more');
    sections.push(fresh.length + ' new ' + feed.label + ':\n' + shown.join('\n'));

    var maxSeen = fresh.reduce(function (m, it) {
      var t = String(it[feed.timeField] || '');
      return t > m ? t : m;
    }, lastSeen);
    props.setProperty(propKey, maxSeen);
  });

  if (!totalNew) return;
  var subject = 'KYJ-SAT: ' + totalNew + ' new item' + (totalNew === 1 ? '' : 's') + ' to review';
  var body = 'New activity since the last check:\n\n' + sections.join('\n\n') +
    '\n\nOpen the local admin dashboard (python3 scripts/dashboard_server.py) to review and act on these.';
  MailApp.sendEmail(to, subject, body);
}

// One-time setup: open this project at https://script.google.com/, pick
// installAdminDigestTrigger_ from the function dropdown next to "Run", and
// click Run once (you'll be asked to authorize Gmail/MailApp access the
// first time). That schedules sendAdminDigestEmail_ to run automatically
// every 30 minutes on Google's servers — it keeps working even when your
// computer is off. Re-running this is safe: it clears any previous trigger
// for the same function first, so you never end up with duplicates. Set an
// ADMIN_EMAIL Script Property first if you want the digest to go somewhere
// other than yongjoon9981@gmail.com.
function installAdminDigestTrigger_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'sendAdminDigestEmail_') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('sendAdminDigestEmail_').timeBased().everyMinutes(30).create();
}
