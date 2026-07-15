/*
  STUDENT DATA
  ------------
  Your simple "student list." Add, edit, or remove a student by
  editing the STUDENTS array below — nothing else needs to change.

  NOT SECURE — by design: this whole file is sent to every visitor's
  browser, so usernames, passwords, and portal content here are all
  technically visible to anyone who looks at the page source. That's
  fine for a local/low-stakes site (see README.md), but don't put
  real sensitive information in here if this site will be public.
*/

const STUDENTS = [
  {
    username: "alice",
    password: "alice123",
    name: "Alice Johnson",
    portal: {
      announcement: "Great job on your last project! Keep it up.",
      grades: [
        { subject: "Math", grade: "A" },
        { subject: "Science", grade: "B+" },
        { subject: "History", grade: "A-" }
      ]
    }
  },
  {
    username: "bob",
    password: "bob123",
    name: "Bob Smith",
    portal: {
      announcement: "Please remember to bring your permission slip on Monday.",
      grades: [
        { subject: "Math", grade: "B" },
        { subject: "Science", grade: "A" },
        { subject: "History", grade: "B+" }
      ]
    }
  },
  {
    username: "charlie",
    password: "charlie123",
    name: "Charlie Nguyen",
    portal: {
      announcement: "You're all caught up — nothing due this week!",
      grades: [
        { subject: "Math", grade: "A-" },
        { subject: "Science", grade: "A" },
        { subject: "History", grade: "B" }
      ]
    }
  }
];
