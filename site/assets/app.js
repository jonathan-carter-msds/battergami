/* battergami dashboard — shared helpers */

const TWITTER_HANDLE = "battergami";

const NAV = [
  ["index.html", "Latest"],
  ["leaderboard.html", "Leaderboard"],
  ["calendar.html", "Calendar"],
  ["check.html", "Check a Line"],
  ["board.html", "Board"],
  ["random.html", "Random"],
  ["about.html", "About"],
];

function renderNav(active) {
  const links = NAV.map(([href, label]) => {
    const cls = href === active ? ' class="active"' : "";
    return `<a href="${href}"${cls}>${label}</a>`;
  }).join("");
  document.body.insertAdjacentHTML("afterbegin", `
    <header class="nav"><div class="inner">
      <a class="brand" href="index.html">&#9918; BATTERGAMI</a>
      <nav>${links}
        <a href="https://x.com/${TWITTER_HANDLE}" target="_blank" rel="noopener">Twitter &#8599;</a>
      </nav>
    </div></header>`);
}

async function fetchJSON(path) {
  const res = await fetch(path, { cache: "no-cache" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

/* Format a 10-value vector [ab,r,h,2b,3b,hr,bb,so,rbi,sb] the same way the
   bot's tweets do: AB/R shown only if non-zero, H and RBI always, the rest
   only if non-zero. */
function fmtLine(v) {
  const [ab, r, h, d, t, hr, bb, so, rbi, sb] = v;
  const p = [];
  if (ab) p.push(`${ab} AB`);
  if (r) p.push(`${r} R`);
  p.push(`${h} H`);
  if (d) p.push(`${d} 2B`);
  if (t) p.push(`${t} 3B`);
  if (hr) p.push(`${hr} HR`);
  p.push(`${rbi} RBI`);
  if (bb) p.push(`${bb} BB`);
  if (so) p.push(`${so} SO`);
  if (sb) p.push(`${sb} SB`);
  return p.join("  |  ");
}

function ordinal(n) {
  if (n == null) return "";
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function fmtDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m - 1];
  return `${mon} ${d}, ${y}`;
}

function commas(n) {
  return (n ?? 0).toLocaleString("en-US");
}

function genLine(el, iso) {
  if (!iso) return;
  el.textContent = "Site data regenerated " + fmtDate(iso) +
    " · box scores via MLB Stats API, history via Retrosheet";
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
