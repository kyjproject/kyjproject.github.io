/*
  STUDENT DATA
  ------------
  Your simple "student list." Add, edit, or remove a student by
  editing the STUDENTS array below — nothing else needs to change.

  Each student's portal is just their own Notion page, embedded in
  an iframe. To get a student's notionUrl:
    1. In Notion, open their page -> Share -> Publish tab -> Publish.
    2. Click "Embed this page" -> "Copy this code".
    3. That gives you a snippet with <iframe src="...">. Copy just
       the URL inside the quotes and paste it below.

  NOT SECURE — by design: this whole file is sent to every visitor's
  browser, so usernames, passwords, and every notionUrl below are
  all technically visible to anyone who looks at the page source.
  That's fine for a local/low-stakes site (see README.md).

  One extra thing worth knowing: embedding requires each Notion page
  to be published/public, so the Notion page itself is viewable by
  anyone with that link, whether or not they ever log into this
  portal. The login here only controls which link loads on which
  portal page — it doesn't make the linked Notion page private.
*/

const STUDENTS = [
  {
    username: "alice",
    password: "alice123",
    name: "Alice Johnson",
    portal: {
      notionUrl: "https://your-workspace.notion.site/REPLACE-WITH-ALICES-PAGE"
    }
  },
  {
    username: "bob",
    password: "bob123",
    name: "Bob Smith",
    portal: {
      notionUrl: "https://your-workspace.notion.site/REPLACE-WITH-BOBS-PAGE"
    }
  },
  {
    username: "charlie",
    password: "charlie123",
    name: "Charlie Nguyen",
    portal: {
      notionUrl: "https://your-workspace.notion.site/REPLACE-WITH-CHARLIES-PAGE"
    }
  }
];
