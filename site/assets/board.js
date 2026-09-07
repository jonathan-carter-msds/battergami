/* board.html — AB x H grid. Colour = average RBI, opacity = how often that
   AB/H square happens at all. Built entirely from data/lines.json. */

const MAX_AB = 7;   // rows 0..7, top row is "7+"
const MAX_H = 6;    // cols 0..6, last col is "6+"

function lerp(a, b, t) { return a + (b - a) * t; }
function mix(c1, c2, t) {
  return `rgb(${Math.round(lerp(c1[0], c2[0], t))},${Math.round(lerp(c1[1], c2[1], t))},${Math.round(lerp(c1[2], c2[2], t))})`;
}
// green -> gold -> red, keyed on average RBI (0 .. cap)
function rbiColor(avg, cap) {
  const t = Math.max(0, Math.min(1, avg / cap));
  const green = [46, 125, 50], gold = [212, 197, 58], red = [198, 40, 40];
  return t < 0.5 ? mix(green, gold, t / 0.5) : mix(gold, red, (t - 0.5) / 0.5);
}

(async () => {
  const host = document.getElementById("board");
  let rows;
  try {
    const d = await fetchJSON("data/lines.json");
    rows = d.rows;
    const gen = document.getElementById("gen");
    if (gen) genLine(gen, d.generated_at);
  } catch (e) { host.innerHTML = `<p class="muted">Couldn't load the board.</p>`; return; }

  // cell[ab][h] = { games, rbiSum }
  const cell = Array.from({ length: MAX_AB + 1 }, () =>
    Array.from({ length: MAX_H + 1 }, () => ({ games: 0, rbiSum: 0 })));

  for (const r of rows) {
    const ab = Math.min(r[0], MAX_AB);
    const h = Math.min(r[2], MAX_H);
    const c = cell[ab][h];
    c.games += r[10];
    c.rbiSum += r[8] * r[10];
  }

  let maxGames = 0, maxAvg = 0;
  for (const row of cell) for (const c of row) {
    if (c.games > maxGames) maxGames = c.games;
    if (c.games) maxAvg = Math.max(maxAvg, c.rbiSum / c.games);
  }
  const cap = Math.max(1, Math.ceil(maxAvg));
  const logMax = Math.log10(maxGames + 1);

  let html = `<table class="board"><thead><tr><th>AB \\ H</th>`;
  for (let h = 0; h <= MAX_H; h++) html += `<th>${h === MAX_H ? h + "+" : h}</th>`;
  html += `</tr></thead><tbody>`;
  for (let ab = MAX_AB; ab >= 0; ab--) {
    html += `<tr><th>${ab === MAX_AB ? ab + "+" : ab}</th>`;
    for (let h = 0; h <= MAX_H; h++) {
      const c = cell[ab][h];
      if (!c.games) { html += `<td></td>`; continue; }
      const avg = c.rbiSum / c.games;
      const op = 0.12 + 0.88 * (Math.log10(c.games + 1) / logMax);
      html += `<td><div class="board-cell"
        style="background:${rbiColor(avg, cap)};opacity:${op.toFixed(3)}"
        data-tip="${ab === MAX_AB ? ab + "+" : ab} AB, ${h === MAX_H ? h + "+" : h} H — ${commas(c.games)} games, ${avg.toFixed(2)} avg RBI"></div></td>`;
    }
    html += `</tr>`;
  }
  html += `</tbody></table>`;
  host.innerHTML = html;

  document.getElementById("cap").textContent = cap.toFixed(0);

  const tip = document.getElementById("tooltip");
  host.addEventListener("mousemove", e => {
    const t = e.target.closest("[data-tip]");
    if (!t) { tip.style.opacity = 0; return; }
    tip.textContent = t.dataset.tip;
    tip.style.left = (e.clientX + 14) + "px";
    tip.style.top = (e.clientY + 14) + "px";
    tip.style.opacity = 1;
  });
  host.addEventListener("mouseleave", () => tip.style.opacity = 0);
})();
