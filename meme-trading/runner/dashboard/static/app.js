/* Runner Intel Dashboard — fetch, render, poll, filter */

const POLL_INTERVAL = 15000;
let currentDetailId = null;
let allScores = [];
let activeVerdictFilter = 'all';
let filterPos = false;
let filterSc = false;

// ── fetch ───────────────────────────────────────────────────────

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  } catch (e) {
    console.warn('fetch failed:', url, e);
    return null;
  }
}

// ── formatting ──────────────────────────────────────────────────

function formatPnl(val) {
  if (val == null) return '<span class="pnl-nil">—</span>';
  const s = val >= 0 ? '+' : '';
  return `<span class="${val >= 0 ? 'pnl-pos' : 'pnl-neg'}">${s}${val.toFixed(1)}%</span>`;
}

function verdictPill(verdict) {
  return `<span class="pill pill-${verdict}">${verdict.replace(/_/g, ' ')}</span>`;
}

function actionTag(action) {
  return `<span class="tag tag-${action}">${action}</span>`;
}

function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function scoreColor(v) {
  return {ignore:'#64748b', watch:'#fbbf24', strong_candidate:'#4ade80', probable_runner:'#60a5fa'}[v] || '#64748b';
}

// ── filter logic (client-side) ──────────────────────────────────

function setFilter(verdict) {
  activeVerdictFilter = verdict;
  document.querySelectorAll('#filter-bar [data-filter]').forEach(b => {
    b.classList.toggle('active', b.dataset.filter === verdict);
  });
  applyFilters();
}

function toggleFilter(type) {
  if (type === 'pos') { filterPos = !filterPos; document.getElementById('filter-pos').classList.toggle('active', filterPos); }
  if (type === 'sc') { filterSc = !filterSc; document.getElementById('filter-sc').classList.toggle('active', filterSc); }
  applyFilters();
}

function applyFilters() {
  let filtered = allScores;
  if (activeVerdictFilter !== 'all') filtered = filtered.filter(s => s.verdict === activeVerdictFilter);
  if (filterPos) filtered = filtered.filter(s => s.has_position);
  if (filterSc) filtered = filtered.filter(s => s.short_circuited);
  renderScoresRows(filtered);
}

// ── render: stats ───────────────────────────────────────────────

function renderStats(d) {
  if (!d) return;
  document.getElementById('stat-total').textContent = d.total_scored ?? '—';
  document.getElementById('stat-strong').textContent = (d.by_verdict||{}).strong_candidate ?? 0;
  document.getElementById('stat-runner').textContent = (d.by_verdict||{}).probable_runner ?? 0;
  document.getElementById('stat-open').textContent = d.open_positions ?? 0;
  document.getElementById('stat-closed').textContent = d.closed_positions ?? 0;
  const el = document.getElementById('stat-pnl');
  if (d.avg_pnl_closed != null) {
    const s = d.avg_pnl_closed >= 0 ? '+' : '';
    el.innerHTML = `<span class="${d.avg_pnl_closed>=0?'pnl-pos':'pnl-neg'} stat-value">${s}${d.avg_pnl_closed.toFixed(1)}%</span>`;
  } else { el.textContent = '—'; el.className = 'stat-value text-slate-600'; }
}

// ── render: scores table ────────────────────────────────────────

function renderScores(scores) {
  allScores = scores || [];
  applyFilters();
}

function renderScoresRows(scores) {
  const tb = document.getElementById('scores-table');
  if (!scores.length) {
    tb.innerHTML = '<tr><td colspan="8" class="p-5 text-slate-700 text-center text-xs ui">No scored candidates match filters</td></tr>';
    return;
  }
  tb.innerHTML = scores.map(s => {
    const cautionHtml = (!s.top_caution || s.top_caution === 'None')
      ? '<span class="caution-none">—</span>'
      : `<span class="caution-inline" title="${esc(s.top_caution)}">${esc(s.top_caution)}</span>`;
    return `
    <tr class="clickable" onclick="showDetail(${s.id})">
      <td class="text-slate-600 text-[11px]">${formatTime(s.created_at)}</td>
      <td><code class="text-slate-300 text-[13px]">${esc(s.short_token)}</code></td>
      <td class="text-right text-[15px] font-bold" style="color:${scoreColor(s.verdict)}">${s.runner_score.toFixed(1)}</td>
      <td>${verdictPill(s.verdict)}</td>
      <td class="text-slate-500 text-[11px] truncate max-w-[220px]" title="${esc(s.top_reason)}">${esc(s.top_reason)}</td>
      <td class="truncate max-w-[170px]">${cautionHtml}</td>
      <td class="text-center">${s.has_position ? '<span class="text-green-500/60 text-[8px]">&#11044;</span>' : ''}</td>
      <td class="text-center">${s.short_circuited ? '<span class="text-red-500/50 text-[8px]">&#11044;</span>' : ''}</td>
    </tr>`;
  }).join('');
}

// ── render: positions table ─────────────────────────────────────

function renderPositions(positions) {
  const tb = document.getElementById('positions-table');
  if (!positions || !positions.length) {
    tb.innerHTML = '<tr><td colspan="12" class="p-4 text-slate-700 text-center text-xs ui">No paper positions yet</td></tr>';
    return;
  }
  tb.innerHTML = positions.map(p => `
    <tr>
      <td><code class="text-slate-400">${esc(p.symbol || p.short_token)}</code></td>
      <td class="text-slate-600">${formatTime(p.signal_time)}</td>
      <td>${verdictPill(p.verdict)}</td>
      <td class="text-right text-slate-400">${p.entry_price_usd != null ? '$'+p.entry_price_usd.toPrecision(3) : '—'}</td>
      <td class="text-right">${formatPnl(p.pnl_5m)}</td>
      <td class="text-right">${formatPnl(p.pnl_30m)}</td>
      <td class="text-right">${formatPnl(p.pnl_1h)}</td>
      <td class="text-right">${formatPnl(p.pnl_4h)}</td>
      <td class="text-right">${formatPnl(p.pnl_24h)}</td>
      <td class="text-right">${formatPnl(p.mfe)}</td>
      <td class="text-right">${formatPnl(p.mae)}</td>
      <td class="text-center"><span class="pill ${p.status==='open'?'pill-watch':'pill-ignore'}">${p.status}</span></td>
    </tr>
  `).join('');
}

// ── detail panel ────────────────────────────────────────────────

async function showDetail(id) {
  currentDetailId = id;
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');
  const title = document.getElementById('detail-title');
  panel.classList.remove('hidden');
  content.innerHTML = '<p class="text-slate-700 text-xs ui">Loading...</p>';

  const d = await fetchJSON(`/api/scores/${id}`);
  if (!d) { content.innerHTML = '<p class="text-red-500/70 text-xs">Failed to load</p>'; return; }

  title.innerHTML = `<code class="text-slate-200 mr-2 text-[14px]">${esc(d.short_token)}</code>${verdictPill(d.verdict)}<span class="text-slate-600 text-[11px] ml-2">${d.runner_score.toFixed(1)} pts</span>`;

  let h = '<div class="grid grid-cols-2 gap-8">';

  // Left: dimensions
  h += '<div>';
  h += '<div class="detail-section">';
  h += '<div class="detail-label">Score Breakdown</div>';
  const dims = ['wallet_quality','cluster_quality','entry_quality','holder_quality','rug_risk','follow_through','narrative'];
  for (const k of dims) {
    const dm = (d.dimensions||{})[k]; if (!dm) continue;
    const lbl = k.replace(/_/g,' ').replace(/\b\w/g, c=>c.toUpperCase());
    const pct = Math.max(0, Math.min(100, dm.score));
    const clr = dm.score >= 60 ? '#4ade80' : dm.score >= 40 ? '#d4a017' : '#f87171';
    h += `<div class="mb-2.5">
      <div class="flex justify-between text-[11px] mb-1">
        <span class="text-slate-400">${lbl}</span>
        <span><span class="text-slate-200 font-bold">${dm.score.toFixed(0)}</span><span class="text-slate-700 mx-1">x${dm.weight.toFixed(2)}</span><span class="text-slate-500">= ${dm.weighted.toFixed(1)}</span></span>
      </div>
      <div class="score-track"><div class="score-bar" style="width:${pct}%;background:${clr}"></div></div>
    </div>`;
  }
  h += '</div>';
  // Raw risk
  h += '<div class="detail-section">';
  h += '<div class="detail-label">Raw Risk</div>';
  h += `<div class="flex gap-8 text-[11px]">
    <div><span class="text-slate-600">Rug</span> <span class="text-slate-200 font-bold">${d.raw_rug_risk??'—'}</span></div>
    <div><span class="text-slate-600">Insider</span> <span class="text-slate-200 font-bold">${d.raw_insider_risk??'—'}</span></div>
  </div></div>`;
  h += '</div>';

  // Right: reasons, cautions, cluster, position
  h += '<div>';
  // Reasons
  h += '<div class="detail-section">';
  h += '<div class="detail-label">Top Reasons</div>';
  for (const r of (d.top_reasons||[])) {
    const bw = Math.min(100, r.weighted / 18 * 100);
    h += `<div class="flex items-center gap-2 mb-2 text-[11px]">
      <span class="text-slate-300 w-28 shrink-0">${esc(r.name)}</span>
      <div class="flex-1 score-track"><div class="score-bar" style="width:${bw}%;background:#4ade80"></div></div>
      <span class="text-slate-600 w-14 text-right">${r.weighted.toFixed(1)}</span>
    </div>`;
  }
  h += '</div>';
  // Cautions
  h += '<div class="detail-section">';
  h += '<div class="detail-label">Cautions</div>';
  for (const c of (d.cautions||[])) {
    if (c === 'None') { h += '<div class="text-[11px] text-slate-700">No major cautions</div>'; }
    else { h += `<div class="caution-detail">${esc(c)}</div>`; }
  }
  h += '</div>';
  // Cluster
  h += '<div class="detail-section">';
  h += '<div class="detail-label">Cluster</div>';
  const cl = d.cluster||{};
  const tiers = Array.isArray(cl.tier_counts) ? cl.tier_counts.join(', ') : JSON.stringify(cl.tier_counts||{});
  h += `<div class="text-[11px] text-slate-500">${cl.wallet_count||0} wallets &middot; ${(cl.convergence_minutes||0).toFixed(0)} min &middot; ${tiers}</div>`;
  h += '</div>';
  // Position
  if (d.position) {
    const p = d.position;
    h += '<div class="detail-section">';
    h += '<div class="detail-label">Position</div>';
    h += `<div class="text-[11px] text-slate-500 mb-1.5">Entry $${(p.entry_price_usd||0).toPrecision(3)} &middot; ${p.amount_sol} SOL &middot; <span class="pill pill-${p.status==='open'?'watch':'ignore'}">${p.status}</span></div>`;
    const ms = ['5m','30m','1h','4h','24h'].filter(m => p['pnl_'+m] != null);
    if (ms.length) {
      h += '<div class="flex gap-4 text-[11px] mb-1">';
      for (const m of ms) h += `<div><span class="text-slate-600">${m}</span> ${formatPnl(p['pnl_'+m])}</div>`;
      h += '</div>';
    }
    h += `<div class="flex gap-5 text-[11px]"><div><span class="text-slate-600">MFE</span> ${formatPnl(p.mfe)}</div><div><span class="text-slate-600">MAE</span> ${formatPnl(p.mae)}</div></div>`;
    h += '</div>';
  }
  // Version + links
  h += `<div class="flex items-center justify-between mt-1 pt-3 border-t border-border">
    <span class="text-[9px] text-slate-700 ui">v${esc(d.scoring_version)} &middot; ${esc(d.weights_hash)}</span>
    <div class="flex gap-2">
      <a href="${d.links?.dexscreener||'#'}" target="_blank" class="ext-link">DexScreener</a>
      <a href="${d.links?.solscan||'#'}" target="_blank" class="ext-link">Solscan</a>
    </div></div>`;
  h += '</div></div>';
  content.innerHTML = h;
}

function closeDetail() {
  document.getElementById('detail-panel').classList.add('hidden');
  currentDetailId = null;
}

// ── render: wallet activity ─────────────────────────────────────

function renderWalletActivity(d) {
  if (!d) return;
  document.getElementById('wallet-total').textContent = d.total_tracked ?? '—';
  document.getElementById('wallet-added').textContent = d.added_6h ?? 0;
  document.getElementById('wallet-deactivated').textContent = d.deactivated_6h ?? 0;
  document.getElementById('wallet-last-sync').textContent = d.last_event_time ? formatTime(d.last_event_time) : '—';

  const tb = document.getElementById('wallet-events-table');
  const ev = d.events || [];
  if (!ev.length) {
    tb.innerHTML = '<tr><td colspan="5" class="p-3 text-slate-700 text-center text-xs ui">No wallet events</td></tr>';
    return;
  }
  tb.innerHTML = ev.map(e => `
    <tr>
      <td class="text-slate-600">${formatTime(e.created_at)}</td>
      <td><code class="text-slate-500">${esc(e.short_address)}</code></td>
      <td>${actionTag(e.action)}</td>
      <td class="text-slate-600">${esc(e.source||'—')}</td>
      <td class="text-slate-600">${esc(e.label||'—')}</td>
    </tr>
  `).join('');
}

// ── polling ─────────────────────────────────────────────────────

async function refreshAll() {
  const ind = document.getElementById('status-indicator');
  const dot = document.getElementById('live-dot');
  try {
    const [stats, scores, positions, wallets] = await Promise.all([
      fetchJSON('/api/stats'),
      fetchJSON('/api/scores?limit=50'),
      fetchJSON('/api/positions?limit=50'),
      fetchJSON('/api/wallets?limit=20'),
    ]);
    renderStats(stats);
    renderScores(scores?.scores);
    renderPositions(positions?.positions);
    renderWalletActivity(wallets);
    ind.textContent = new Date().toLocaleTimeString();
    ind.className = 'text-[9px] text-slate-700 ui whitespace-nowrap';
    dot.className = 'w-1.5 h-1.5 rounded-full bg-emerald-500 mt-0.5';
  } catch (e) {
    ind.textContent = 'error';
    ind.className = 'text-[9px] text-red-500 ui';
    dot.className = 'w-1.5 h-1.5 rounded-full bg-red-500 mt-0.5';
  }
}

refreshAll();
setInterval(refreshAll, POLL_INTERVAL);
