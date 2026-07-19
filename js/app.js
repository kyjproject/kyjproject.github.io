/*
  APP LOGIC
  ---------
  Shared functions used across the site.

  You shouldn't need to edit this file much:
    - To add/edit/remove students or their links, edit js/data.js.
    - To change the footer's social icons, edit js/social-links.js.
    - Both portal.html and calendar.html call renderEmbedPage() at
      the bottom of this file — it's shared, since both pages are
      just "logged-in student sees one iframe" with a different URL.
    - To add a THIRD page like this later (assignments, a syllabus,
      whatever), copy portal.html, give its iframe the id
      "embed-frame" (same id, new page), and call renderEmbedPage()
      with that page's own URL field from js/data.js.
*/

const SESSION_KEY = "loggedInUsername";

// Pages login.html is allowed to send someone back to after they log
// in. Keeps a crafted "?redirect=" link from sending someone to an
// external site or a javascript: URL.
const REDIRECTABLE_PAGES = ["index.html", "portal.html", "calendar.html", "resources.html", "philosophy.html", "faq.html"];

// Checks a username/password against STUDENTS (from data.js).
// On success, remembers who's logged in (in this browser) and
// returns true. Returns false on a bad username/password.
function login(username, password) {
  const match = STUDENTS.find(function (s) {
    return s.username === username && s.password === password;
  });

  if (match) {
    localStorage.setItem(SESSION_KEY, match.username);
    return true;
  }
  return false;
}

// Returns the currently logged-in student object, or null if
// nobody is logged in on this browser.
function getCurrentStudent() {
  const username = localStorage.getItem(SESSION_KEY);
  if (!username) return null;
  return STUDENTS.find(function (s) { return s.username === username; }) || null;
}

// Sends the visitor to the login page if nobody is logged in,
// remembering the page they were trying to reach so login.html can
// send them back afterward. Call this at the very top of any
// "protected" page.
function requireLogin() {
  if (!getCurrentStudent()) {
    const here = window.location.pathname.split("/").pop();
    window.location.href = "login.html?redirect=" + encodeURIComponent(here);
  }
}

// Reads "?redirect=" from the login page's URL and returns it only
// if it's one of this site's own pages — otherwise sends the visitor
// home. Call this after a successful login.
function getLoginRedirect() {
  const requested = new URLSearchParams(window.location.search).get("redirect");
  return REDIRECTABLE_PAGES.includes(requested) ? requested : "index.html";
}

// Forgets who's logged in and returns to the home page.
function logout() {
  localStorage.removeItem(SESSION_KEY);
  window.location.href = "index.html";
}

// Renders the footer's social icons from SOCIAL_LINKS (defined in
// js/social-links.js). Called near the bottom of every page.
function renderSocialLinks() {
  const container = document.getElementById("social-links");
  if (!container) return;

  container.innerHTML = SOCIAL_LINKS.map(function (s) {
    return '<a href="' + s.url + '" target="_blank" rel="noopener" ' +
           'aria-label="' + s.name + '" class="social-icon">' +
             '<img src="https://cdn.simpleicons.org/' + s.icon + '/8F8F8F?viewbox=auto" ' +
             'alt="" width="20" height="20" loading="lazy">' +
           '</a>';
  }).join("");
}

// Swaps the nav's "Log In" button for the logged-in student's name.
// Call this near the bottom of any public page that has the
// site-header nav (index.html, philosophy.html, resources.html,
// faq.html).
function renderNavAuth() {
  const loginBtn = document.getElementById("nav-login-btn");
  const userName = document.getElementById("nav-user-name");
  if (!loginBtn || !userName) return;

  const student = getCurrentStudent();
  if (student) {
    loginBtn.hidden = true;
    userName.hidden = false;
    userName.textContent = student.name;
  } else {
    loginBtn.hidden = false;
    userName.hidden = true;
  }
}

// Fills in the shared bar's "Open in ... ↗" direct link and points
// this page's iframe at the given URL. portal.html and calendar.html
// each call this with their own URL and a label — that's the only
// difference between the two pages.
function renderEmbedPage(student, embedUrl, label) {
  if (!student) return;

  const frame = document.getElementById("embed-frame");
  frame.src = embedUrl;
  frame.title = student.name + "'s " + label;

  const link = document.getElementById("open-direct-link");
  link.href = embedUrl;
  link.textContent = "Open in " + label + " ↗";
}
