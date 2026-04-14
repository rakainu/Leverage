/* Runner Intel Dashboard — fetch, render, poll */

const POLL_INTERVAL = 15000;
let currentDetailId = null;

// ── fetch helper ────────────────────────────────────────────────

async function fetchJSON(url) {
  try {
    const resp = await fetch(url);
    if (!resp.ok) return null;
    return await resp.json();
  } catch (e) {
    console.warn('fetch failed:', url, e);
    return null;
  }
}

// ── formatting helpers ──────────────────────────────────────────

function formatPnl(val) {
  if (val == null) return '<span class="pnl-nil">—</span>';
  const sign = val >= 0 ? '+' : '';
  const cls = val >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `<span class="${cls}">${sign}${val.toFixed(1)}%</span>`;
}

function verdictPill(verdict) {
  const label = verdict.replace(/_/g, ' ');
  return `<span class="pill pill-${verdict}">${label}</span>`;
}

function actionPill(action) {
  return `<span class="action-pill action-${action}">${action}</span>`;
}

function formatTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${mo}-${dd} ${hh}:${mm}`;
}

function escapeHtml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function scoreColor(verdict) {
  const map = { ignore: '#9ca3af', watch: '#fbbf24', strong_candidate: '#4ade80', probable_runner: '#60a5fa' };
  return map[verdict] || '#94a3b8';
}

function cautionSpan(text) {
  if (!text || text === 'None') return '<span class="caution-none">—</span>';
  return `<span class="caution-text">${escapeHtml(text)}</span>`;
}

// ── render: stats cards ─────────────────────────────────────────

function renderStats(data) {
  if (!data) return;
  document.getElementById('stat-total').textContent = data.total_scored ?? '—';
  document.getElementById('stat-strong').textContent = (data.by_verdict || {}).strong_candidate ?? 0;
  document.getElementById('stat-runner').textContent = (data.by_verdict || {}).probable_runner ?? 0;
  document.getElementById('stat-open').textContent = data.open_positions ?? 0;
  document.getElementById('stat-closed').textContent = data.closed_positions ?? 0;
  const pnlEl = document.getElementById('stat-pnl');
  if (data.avg_pnl_closed != null) {
    const sign = data.avg_pnl_closed >= 0 ? '+' : '';
    pnlEl.innerHTML = `<span class="${data.avg_pnl_closed >= 0 ? 'pnl-pos' : 'pnl-neg'} stat-value">${sign}${data.avg_pnl_closed.toFixed(1)}%</span>`;
  } else {
    pnlEl.textContent = '—';
    pnlEl.className = 'stat-value text-slate-300';
  }
}

// ── render: scores table ────────────────────────────────────────

function renderScores(scores) {
  const tbody = document.getElementById('scores-table');
  if (!scores || !scores.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="p-4 text-slate-600 text-center text-xs">No scored candidates yet</td></tr>';
    return;
  }
  tbody.innerHTML = scores.map(s => `
    <tr class="clickable" onclick="showDetail(${s.id})">
      <td class="text-slate-300">${formatTime(s.created_at)}</td>
      <td><code class="text-slate-300">${escapeHtml(s.short_token)}</code></td>
      <td class="text-right font-bold" style="color:${scoreColor(s.verdict)}">${s.runner_score.toFixed(1)}</td>
      <td>${verdictPill(s.verdict)}</td>
      <td class="text-slate-200 truncate max-w-[200px]" title="${escapeHtml(s.top_reason)}">${escapeHtml(s.top_reason)}</td>
      <td>${cautionSpan(s.top_caution)}</td>
      <td class="text-center">${s.has_position ? '<span class="text-green-400 text-xs">&#9679;</span>' : ''}</td>
      <td class="text-center">${s.short_circuited ? '<span class="text-red-400 text-xs">&#9679;</span>' : ''}</td>
    </tr>
  `).join('');
}

// ── render: positions table ─────────────────────────────────────

function renderPositions(positions) {
  const tbody = document.getElementById('positions-table');
  if (!positions || !positions.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="p-4 text-slate-600 text-center text-xs">No paper positions yet</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(p => {
    const statusCls = p.status === 'open' ? 'pill-watch' : 'pill-ignore';
    return `
    <tr>
      <td><code class="text-slate-300">${escapeHtml(p.symbol || p.short_token)}</code></td>
      <td class="text-slate-300">${formatTime(p.signal_time)}</td>
      <td>${verdictPill(p.verdict)}</td>
      <td class="text-right text-slate-300">${p.entry_price_usd != null ? '$' + p.entry_price_usd.toPrecision(3) : '—'}</td>
      <td class="text-right">${formatPnl(p.pnl_5m)}</td>
      <td class="text-right">${formatPnl(p.pnl_30m)}</td>
      <td class="text-right">${formatPnl(p.pnl_1h)}</td>
      <td class="text-right">${formatPnl(p.pnl_4h)}</td>
      <td class="text-right">${formatPnl(p.pnl_24h)}</td>
      <td class="text-right">${formatPnl(p.mfe)}</td>
      <td class="text-right">${formatPnl(p.mae)}</td>
      <td class="text-center"><span class="pill ${statusCls}">${p.status}</span></td>
    </tr>
  `}).join('');
}

// ── detail panel ────────────────────────────────────────────────

async function showDetail(scoreId) {
  currentDetailId = scoreId;
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');
  const title = document.getElementById('detail-title');

  panel.classList.remove('hidden');
  content.innerHTML = '<p class="text-slate-600 text-xs">Loading...</p>';

  const d = await fetchJSON(`/api/scores/${scoreId}`);
  if (!d) {
    content.innerHTML = '<p class="text-red-400 text-xs">Failed to load detail</p>';
    return;
  }

  title.innerHTML = `<code class="text-slate-300 mr-2">${escapeHtml(d.short_token)}</code> ${verdictPill(d.verdict)} <span class="text-slate-300 text-xs ml-2">${d.runner_score.toFixed(1)} pts</span>`;

  let html = '<div class="grid grid-cols-2 gap-6">';

  // Left: score breakdown
  html += '<div>';
  html += '<div class="detail-section">';
  html += '<div class="detail-label">Score Breakdown</div>';
  const dimOrder = ['wallet_quality', 'cluster_quality', 'entry_quality', 'holder_quality', 'rug_risk', 'follow_through', 'narrative'];
  for (const key of dimOrder) {
    const dim = (d.dimensions || {})[key];
    if (!dim) continue;
    const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const pct = Math.max(0, Math.min(100, dim.score));
    const barColor = dim.score >= 60 ? '#4ade80' : dim.score >= 40 ? '#fbbf24' : '#f87171';
    html += `
      <div class="mb-2">
        <div class="flex justify-between text-[13px] mb-1">
          <span class="text-slate-200">${label}</span>
          <span class="text-slate-300">${dim.score.toFixed(0)} <span class="text-slate-600">x${dim.weight.toFixed(2)}</span> = <span class="text-slate-300">${dim.weighted.toFixed(1)}</span></span>
        </div>
        <div class="score-track">
          <div class="score-bar" style="width:${pct}%; background:${barColor}"></div>
        </div>
      </div>
    `;
  }
  html += '</div>'; // detail-section

  // Raw scores
  html += '<div class="detail-section">';
  html += '<div class="detail-label">Raw Risk Scores</div>';
  html += `<div class="flex gap-6 text-[13px]">`;
  html += `<div><span class="text-slate-300">Rug Risk</span> <span class="text-slate-200 font-bold ml-1">${d.raw_rug_risk ?? '—'}</span></div>`;
  html += `<div><span class="text-slate-300">Insider Risk</span> <span class="text-slate-200 font-bold ml-1">${d.raw_insider_risk ?? '—'}</span></div>`;
  html += `</div></div>`;

  html += '</div>'; // left column

  // Right: reasons + cautions + meta
  html += '<div>';

  // Top 3 reasons
  html += '<div class="detail-section">';
  html += '<div class="detail-label">Top Reasons</div>';
  for (const r of (d.top_reasons || [])) {
    const barW = Math.min(100, r.weighted / 20 * 100); // normalize to max ~20
    html += `<div class="flex items-center gap-2 mb-1.5 text-[13px]">
      <span class="text-slate-300 w-28 shrink-0">${escapeHtml(r.name)}</span>
      <div class="flex-1 score-track"><div class="score-bar" style="width:${barW}%; background:#4ade80"></div></div>
      <span class="text-slate-300 w-16 text-right">${r.score.toFixed(0)} (${r.weighted.toFixed(1)})</span>
    </div>`;
  }
  html += '</div>';

  // Cautions
  html += '<div class="detail-section">';
  html += '<div class="detail-label">Cautions</div>';
  for (const c of (d.cautions || [])) {
    if (c === 'None') {
      html += '<div class="text-[13px] text-slate-600">No major cautions</div>';
    } else {
      html += `<div class="text-[13px] mb-1"><span class="caution-text">${escapeHtml(c)}</span></div>`;
    }
  }
  html += '</div>';

  // Cluster
  html += '<div class="detail-section">';
  html += '<div class="detail-label">Cluster</div>';
  const cl = d.cluster || {};
  const tiers = Array.isArray(cl.tier_counts) ? cl.tier_counts.join(', ') : JSON.stringify(cl.tier_counts || {});
  html += `<div class="text-[13px] text-slate-200">${cl.wallet_count || 0} wallets &middot; ${(cl.convergence_minutes || 0).toFixed(0)} min &middot; tiers: ${tiers}</div>`;
  html += '</div>';

  // Position milestones
  if (d.position) {
    const p = d.position;
    html += '<div class="detail-section">';
    html += '<div class="detail-label">Position</div>';
    html += `<div class="text-[13px] text-slate-200 mb-1">${verdictPill(d.verdict)} &middot; Entry $${(p.entry_price_usd || 0).toPrecision(3)} &middot; ${p.amount_sol} SOL &middot; <span class="pill pill-${p.status === 'open' ? 'watch' : 'ignore'}">${p.status}</span></div>`;

    const milestones = ['5m', '30m', '1h', '4h', '24h'];
    const captured = milestones.filter(m => p['pnl_' + m] != null);
    if (captured.length) {
      html += '<div class="flex gap-3 text-[13px] mt-1">';
      for (const m of captured) {
        html += `<div class="text-slate-300">${m}: ${formatPnl(p['pnl_' + m])}</div>`;
      }
      html += '</div>';
    }
    html += `<div class="flex gap-4 text-[13px] mt-1"><div class="text-slate-300">MFE: ${formatPnl(p.mfe)}</div><div class="text-slate-300">MAE: ${formatPnl(p.mae)}</div></div>`;
    html += '</div>';
  }

  // Version + links
  html += '<div class="flex items-center justify-between mt-2">';
  html += `<span class="text-[10px] text-slate-600">v${escapeHtml(d.scoring_version)} &middot; ${escapeHtml(d.weights_hash)}</span>`;
  html += '<div class="flex gap-2">';
  html += `<a href="${d.links?.dexscreener || '#'}" target="_blank" class="ext-link">DexScreener</a>`;
  html += `<a href="${d.links?.solscan || '#'}" target="_blank" class="ext-link">Solscan</a>`;
  html += '</div></div>';

  html += '</div>'; // right column
  html += '</div>'; // grid
  content.innerHTML = html;
}

function closeDetail() {
  document.getElementById('detail-panel').classList.add('hidden');
  currentDetailId = null;
}

// ── render: wallet activity ──────────────────────────────────────

function renderWalletActivity(data) {
  if (!data) return;
  document.getElementById('wallet-total').textContent = data.total_tracked ?? '—';
  document.getElementById('wallet-added').textContent = data.added_6h ?? 0;
  document.getElementById('wallet-deactivated').textContent = data.deactivated_6h ?? 0;
  document.getElementById('wallet-last-sync').textContent = data.last_event_time ? formatTime(data.last_event_time) : 'no events';

  const tbody = document.getElementById('wallet-events-table');
  const events = data.events || [];
  if (!events.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="p-4 text-slate-600 text-center text-xs">No wallet registry events yet</td></tr>';
    return;
  }
  tbody.innerHTML = events.map(e => `
    <tr>
      <td class="text-slate-300">${formatTime(e.created_at)}</td>
      <td><code class="text-slate-200">${escapeHtml(e.short_address)}</code></td>
      <td>${actionPill(e.action)}</td>
      <td class="text-slate-300">${escapeHtml(e.source || '—')}</td>
      <td class="text-slate-300">${escapeHtml(e.label || '—')}</td>
    </tr>
  `).join('');
}

// ── outcomes ────────────────────────────────────────────────────

function fmtMcap(v) {
  if (v == null) return '—';
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}

function renderOutcomes(data) {
  if (!data) return;
  document.getElementById('out-tracked').textContent = data.tracked ?? '—';
  document.getElementById('out-mooned').textContent = data.mooned ?? '—';
  document.getElementById('out-caught').textContent = data.caught ?? '—';
  const missEl = document.getElementById('out-misses');
  missEl.textContent = data.filter_misses ?? '—';
  missEl.className = (data.filter_misses || 0) > 0
    ? 'stat-value text-red-400'
    : 'stat-value text-slate-300';

  const tbody = document.getElementById('outcomes-table');
  const rows = data.leaderboard || [];
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="p-4 text-slate-600 text-center text-xs">No outcome data yet — tracker polls every 5 min</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const verdictCell = r.is_filter_miss
      ? `<span class="pill pill-ignore" style="background:#7f1d1d;color:#fecaca">MISS · ${r.best_verdict}</span>`
      : verdictPill(r.best_verdict);
    const peakCell = (r.peak_mcap_usd || 0) >= 1_000_000
      ? `<span class="pnl-pos">${fmtMcap(r.peak_mcap_usd)}</span>`
      : fmtMcap(r.peak_mcap_usd);
    return `
      <tr>
        <td><code class="text-slate-200">${escapeHtml(r.short_token)}</code></td>
        <td>${verdictCell}</td>
        <td class="text-right text-slate-300">${fmtMcap(r.entry_mcap_usd)}</td>
        <td class="text-right">${peakCell}</td>
        <td class="text-right text-slate-300">${r.multiple ? r.multiple.toFixed(1) + 'x' : '—'}</td>
        <td class="text-right text-slate-400">${formatTime(r.peak_seen_at)}</td>
      </tr>`;
  }).join('');
}

// ── polling ─────────────────────────────────────────────────────

async function refreshAll() {
  const indicator = document.getElementById('status-indicator');
  const dot = document.getElementById('live-dot');
  try {
    const [stats, scores, positions, wallets, outcomes] = await Promise.all([
      fetchJSON('/api/stats'),
      fetchJSON('/api/scores?limit=50'),
      fetchJSON('/api/positions?limit=50'),
      fetchJSON('/api/wallets?limit=30'),
      fetchJSON('/api/outcomes?limit=50'),
    ]);
    renderStats(stats);
    renderScores(scores?.scores);
    renderPositions(positions?.positions);
    renderWalletActivity(wallets);
    renderOutcomes(outcomes);
    indicator.textContent = new Date().toLocaleTimeString();
    indicator.className = 'text-[10px] text-slate-600 label-text';
    dot.className = 'w-2 h-2 rounded-full bg-green-500';
  } catch (e) {
    indicator.textContent = 'error';
    indicator.className = 'text-[10px] text-red-500 label-text';
    dot.className = 'w-2 h-2 rounded-full bg-red-500';
  }
}

// Initial load + poll
refreshAll();
setInterval(refreshAll, POLL_INTERVAL);
