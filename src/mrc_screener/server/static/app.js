// Vanilla JS, no build step, no framework (see DECISIONS.md D3).
// Fetches row list once, then drives a keyboard-only keep/reject loop.

const el = {
  banner: document.getElementById("banner"),
  position: document.getElementById("position"),
  filename: document.getElementById("filename"),
  counts: document.getElementById("counts"),
  frame: document.getElementById("frame"),
  sliceLabel: document.getElementById("slice-label"),
  viewer: document.getElementById("viewer"),
  done: document.getElementById("done"),
  doneSummary: document.getElementById("done-summary"),
  exportBtn: document.getElementById("export-btn"),
  exportResult: document.getElementById("export-result"),
  help: document.getElementById("help"),
};

let queue = [];
let currentIdx = -1;
let currentRowDetail = null; // {n_frames, n_previews}
let slicePos = 0;
let helpVisible = false;

async function loadState() {
  const res = await fetch("/api/state");
  const state = await res.json();
  queue = state.queue;
  renderBanner(state.issues);
  updateCounts(state.counts);
  const start = findFirstUndecided(0);
  if (start === -1) {
    showDone();
    return;
  }
  await loadRow(start);
}

function renderBanner(issues) {
  const problems = [];
  if (issues.unmatched.length) problems.push(`${issues.unmatched.length} unmatched`);
  if (issues.ambiguous.length) problems.push(`${issues.ambiguous.length} ambiguous`);
  if (issues.file_collisions.length) problems.push(`${issues.file_collisions.length} file collisions`);
  if (issues.unclaimed_files.length) problems.push(`${issues.unclaimed_files.length} unclaimed files`);
  if (problems.length === 0) {
    el.banner.classList.add("hidden");
    return;
  }
  el.banner.textContent =
    `${problems.join(", ")} -- these rows are excluded from screening. ` +
    `Re-run with --overrides or looser matching patterns to include them.`;
  el.banner.classList.remove("hidden");
}

function findFirstUndecided(start) {
  if (queue.length === 0) return -1;
  for (let i = 0; i < queue.length; i++) {
    const idx = (start + i) % queue.length;
    if (queue[idx].decision === "undecided") return idx;
  }
  return -1;
}

async function loadRow(idx) {
  currentIdx = idx;
  el.done.classList.add("hidden");
  el.viewer.classList.remove("hidden");
  const row = queue[idx];
  const res = await fetch(`/api/rows/${encodeURIComponent(row.row_id)}`);
  currentRowDetail = await res.json();
  slicePos = Math.floor(currentRowDetail.n_previews / 2);
  renderFrame();
  renderHeader();
  prefetchNeighbors();
}

function renderHeader() {
  const row = queue[currentIdx];
  el.position.textContent = `${currentIdx + 1} / ${queue.length}`;
  el.filename.textContent = row.filename || row.reference;
}

function renderFrame() {
  const row = queue[currentIdx];
  el.frame.src = `/api/rows/${encodeURIComponent(row.row_id)}/preview/${slicePos}`;
  el.sliceLabel.textContent = `slice ${slicePos + 1} / ${currentRowDetail.n_previews}`;
}

function updateCounts(counts) {
  el.counts.textContent = `keep ${counts.keep} · reject ${counts.reject}`;
}

function preload(url) {
  const img = new Image();
  img.src = url;
}

function prefetchNeighbors() {
  const row = queue[currentIdx];
  for (const d of [-1, 1]) {
    const p = slicePos + d;
    if (p >= 0 && p < currentRowDetail.n_previews) {
      preload(`/api/rows/${encodeURIComponent(row.row_id)}/preview/${p}`);
    }
  }
  let warmed = 0;
  for (let i = 1; i <= queue.length && warmed < 3; i++) {
    const j = (currentIdx + i) % queue.length;
    if (queue[j].decision !== "undecided") continue;
    warmed++;
    fetch(`/api/rows/${encodeURIComponent(queue[j].row_id)}`)
      .then((r) => r.json())
      .then((info) => {
        const mid = Math.floor(info.n_previews / 2);
        preload(`/api/rows/${encodeURIComponent(queue[j].row_id)}/preview/${mid}`);
      });
  }
}

async function decide(decision) {
  if (currentIdx === -1) return;
  const row = queue[currentIdx];
  const res = await fetch(`/api/rows/${encodeURIComponent(row.row_id)}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
  });
  const data = await res.json();
  row.decision = decision;
  updateCounts(data.counts);
  goToNext();
}

function skip() {
  goToNext();
}

function goToNext() {
  const next = findFirstUndecided((currentIdx + 1) % queue.length);
  if (next === -1) {
    showDone();
    return;
  }
  loadRow(next);
}

async function undo() {
  const res = await fetch("/api/undo", { method: "POST" });
  const data = await res.json();
  updateCounts(data.counts);
  if (!data.row_id) return;
  const idx = queue.findIndex((r) => r.row_id === data.row_id);
  if (idx === -1) return;
  queue[idx].decision = "undecided";
  loadRow(idx);
}

function scrub(delta) {
  if (!currentRowDetail) return;
  const next = slicePos + delta;
  if (next < 0 || next >= currentRowDetail.n_previews) return;
  slicePos = next;
  renderFrame();
  const ahead = slicePos + delta;
  if (ahead >= 0 && ahead < currentRowDetail.n_previews) {
    preload(`/api/rows/${encodeURIComponent(queue[currentIdx].row_id)}/preview/${ahead}`);
  }
}

function showDone() {
  currentIdx = -1;
  currentRowDetail = null;
  el.viewer.classList.add("hidden");
  el.done.classList.remove("hidden");
  const decided = queue.filter((r) => r.decision !== "undecided").length;
  el.doneSummary.textContent = `${decided} / ${queue.length} rows decided.`;
}

function toggleHelp() {
  helpVisible = !helpVisible;
  el.help.classList.toggle("hidden", !helpVisible);
}

document.addEventListener("keydown", (e) => {
  if (e.key === "?") {
    toggleHelp();
    return;
  }
  if (helpVisible) return;
  switch (e.key) {
    case "ArrowRight":
    case "l":
      decide("keep");
      break;
    case "ArrowLeft":
    case "h":
      decide("reject");
      break;
    case "ArrowUp":
      scrub(1);
      break;
    case "ArrowDown":
      scrub(-1);
      break;
    case "u":
      undo();
      break;
    case " ":
      e.preventDefault();
      skip();
      break;
    default:
      break;
  }
});

el.exportBtn.addEventListener("click", async () => {
  const res = await fetch("/api/export", { method: "POST" });
  const data = await res.json();
  el.exportResult.textContent = `Wrote ${data.screened_path} and ${data.kept_path}`;
});

loadState();
