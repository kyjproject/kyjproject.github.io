# Student Portal — GitHub Pages Starter
A minimal student portal for each student to briefly login and get to their dedicated notion pages. 
I decided to create this structure because I thought that it would be better off than just giving very weird links 
that gets automatically assigned when you publish a page in notion. 

## Note that this login is NOT secure — on purpose
Because of the above philosophy, this website is far from secure login credentials. 
Any user who knows that they can inspect a website could easily discover the password and do whatever they want. 
But to be honest that has no point because this is a totally static site. The main content can be only varied via admin account through notion. 

## File structure
```
student-portal/
├── index.html      Home page
├── login.html      Login form
├── portal.html     Welcome bar + embedded Notion iframe (bounces to login.html if not logged in)
├── css/
│   └── style.css   All styling — colors and fonts are CSS variables at the top
├── js/
│   ├── data.js      The "student list" — edit this to add students or change their Notion link
│   └── app.js       Login/logout/portal logic — rarely needs editing
├── .nojekyll        Tells GitHub Pages to serve the files as-is
└── README.md        This file
```

## Adding, editing, or removing a student
Open `js/data.js`. Every student is one entry in the `STUDENTS` list:

```js
{
  username: "alice",
  password: "alice123",
  name: "Alice Johnson",
  portal: {
    notionUrl: "https://your-workspace.notion.site/alice-page"
  }
}
```

## Changing what the portal page shows

`portal.html` has exactly two dynamic pieces, both filled in by
`renderPortal()` near the bottom of `js/app.js`:

- `#welcome-text` — the "Welcome back, [name]" line in the top bar
- `#notion-frame` — the iframe, pointed at `student.portal.notionUrl`

To show something other than a Notion embed, or to add more to the
top bar, edit `renderPortal()` and the matching data in
`js/data.js`.

