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

// Builds the HTML for a student's personal portal page.
// Add, remove, or change the panels below to change what shows up —
// just make sure any new data also exists on each student in
// js/data.js.
function renderPortal(student) {
  if (!student) return;

  const gradeRows = student.portal.grades.map(function (g) {
    return `
      <div class="grade-row">
        <span class="grade-subject">${g.subject}</span>
        <span class="grade-stamp">${g.grade}</span>
      </div>`;
  }).join("");

  document.getElementById("portal-content").innerHTML = `
    <h1>Welcome back, ${student.name}</h1>

    <div class="panel">
      <h2>For You</h2>
      <p>${student.portal.announcement}</p>
    </div>

    <div class="panel">
      <h2>My Grades</h2>
      ${gradeRows}
    </div>
  `;
}
