# Student Portal — GitHub Pages Starter

A small, static student portal. Each student logs in and lands on
their own embedded Notion page. It's plain HTML, CSS, and
JavaScript, so there's no build step and nothing to install: edit
the files and refresh the page.

## ⚠️ This login is NOT secure — on purpose

GitHub Pages only serves static files; there's no server checking
passwords. The "login" is just a JavaScript check against the list
in `js/data.js`, and that whole file is downloaded to every visitor's
browser. Anyone who opens their browser's dev tools (or just views
the page source) can see every student's username, password, and
Notion link.

That's fine for what this was built for — a low-stakes, local site
where the goal is just "students shouldn't casually see each other's
stuff," not real security.

One more thing worth knowing, specific to embedding Notion: a page
has to be published/public for the iframe to show it at all, so each
student's Notion page is technically viewable by anyone with that
link, logged into this portal or not. The login only decides which
link loads on which portal page — it doesn't make the Notion page
itself private. Don't publish anything genuinely sensitive to Notion
for this.

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

## Getting a student's Notion link

1. Open the student's page in Notion.
2. Click **Share** (top right) → the **Publish** tab → **Publish**.
3. Click **Embed this page** → **Copy this code**.
4. That gives you a snippet containing `<iframe src="...">`. Copy
   just the URL inside the quotes.
5. Paste it into that student's `notionUrl` in `js/data.js`.

Note: publishing a page also publishes all of its subpages, so make
sure everything underneath is meant to be public too.

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

Copy an existing entry, change the values, and save. To remove a
student, delete their entry. No other file needs to change.

## Changing what the portal page shows

`portal.html` has exactly two dynamic pieces, both filled in by
`renderPortal()` near the bottom of `js/app.js`:

- `#welcome-text` — the "Welcome back, [name]" line in the top bar
- `#notion-frame` — the iframe, pointed at `student.portal.notionUrl`

To show something other than a Notion embed, or to add more to the
top bar, edit `renderPortal()` and the matching data in
`js/data.js`.

## The dark portal theme

`portal.html` uses a dark background (`#191919`) matched to Notion's
own dark mode, so the embed doesn't have a visible seam. Those
colors live as CSS variables near the top of `css/style.css`
(`--portal-bg`, `--portal-text`, `--portal-border`, `--portal-muted`)
— change them there if you'd like a different look. `index.html` and
`login.html` keep the original light theme; happy to make those dark
too if you'd rather the whole site match.

## Test it locally

Either works:

- **Simplest:** double-click `index.html` to open it in your browser.
- **Closer to production:** from this folder, run
  `python3 -m http.server`, then visit `http://localhost:8000`.

Try logging in with `alice / alice123` (or `bob` / `charlie`, same
pattern) — until you swap in real Notion links, the iframe will show
a "page not found" style message from Notion, which is expected.

## Deploy to GitHub Pages

1. Create a new repository on GitHub.
2. Push the contents of this folder to the repo, so these files sit
   at the repo's root (not inside an extra subfolder).
3. In the repo, go to **Settings → Pages**.
4. Under "Build and deployment," choose **Deploy from a branch**,
   then pick your branch (usually `main`) and the `/ (root)` folder.
5. Save. GitHub gives you a URL like
   `https://your-username.github.io/your-repo-name/` within a minute
   or two.

Every future update is just: edit a file, commit, push — GitHub
Pages redeploys automatically.

## Ideas for later

- Replace the demo students with your real class list and their
  real Notion links.
- Make the whole site dark, not just the portal page.
- If real privacy ever matters — for the login or for keeping each
  student's page truly private — the usual next step is a free auth
  service like Firebase Authentication or Supabase, paired with
  Notion's API instead of public embeds. Happy to help set that up
  if this project grows.
