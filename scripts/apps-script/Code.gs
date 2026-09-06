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
// Fixed repo location (not under cfg.dir, which is only the configurable
// cloud-save directory) — matches scripts/review_staged_questions.py.
function stagingPath_() { return 'db/staging/proposed_questions.json'; }

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
