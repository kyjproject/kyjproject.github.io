/*
  APP LOGIC
  ---------
  Shared functions used by login.html and portal.html.

  You shouldn't need to edit this file much:
    - To add/edit/remove students, edit js/data.js instead.
    - To change what appears on the portal page, edit
      renderPortal() at the bottom of this file.
*/

const SESSION_KEY = "loggedInUsername";

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

// Sends the visitor to the login page if nobody is logged in.
// Call this at the very top of any "protected" page.
function requireLogin() {
  if (!getCurrentStudent()) {
    window.location.href = "login.html";
  }
}

// Forgets who's logged in and returns to the home page.
function logout() {
  localStorage.removeItem(SESSION_KEY);
  window.location.href = "index.html";
}

// Fills in the portal page for one student: the welcome text, and
// the embedded Notion iframe's address. To show something other
// than a Notion embed, this is the only function you need to touch.
function renderPortal(student) {
  if (!student) return;

  document.getElementById("welcome-text").textContent = "Welcome back, " + student.name;

  const frame = document.getElementById("notion-frame");
  frame.src = student.portal.notionUrl;
  frame.title = student.name + "'s Notion portal";
}
