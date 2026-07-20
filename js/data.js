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

// The shared class calendar — the same for every student, unlike
// notionUrl below which is each student's own page. To get this:
// in Google Calendar, go to the calendar's Settings -> "Integrate
// calendar" -> copy the "Embed code" -> paste just the URL inside
// the iframe's src="..." here.
const CALENDAR_URL = "https://calendar.google.com/calendar/embed?src=f378c5925c22121a1f448ff08358c807fce36f9997098326218425ba70498bba%40group.calendar.google.com&ctz=Asia%2FManila";

const STUDENTS = [
  {
    username: "alice",
    password: "alice123",
    name: "Alice Inthe Wonderland",
    portal: {
      notionUrl: "https://embed.embednotion.com/39edb87002c4804c855ec1c9bbb7791d"
    }
  },
  {
    username: "bogue",
    password: "password",
    name: "Bogue Kwon",
    portal: {
      notionUrl: "https://embed.embednotion.com/39edb87002c4804c855ec1c9bbb7791d"
    }
  },
  {
    username: "hamin",
    password: "password",
    name: "Hamin Park",
    portal: {
      notionUrl: "https://your-workspace.notion.site/REPLACE-WITH-CHARLIES-PAGE"
    }
  },
  {
    username : "seohoo",
    password: "password",
    name: "Seohoo Lee",
    portal: {
      notionUrl: "https://wealthy-mapusaurus-5ee.notion.site/3a3db87002c48099b507ea08fa755507?v=59adb87002c482f0b8618874306037c3&pvs=73"
    }
  }
];
