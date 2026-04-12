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

function verdictBadge(verdict) {
  const cls = 'verdict-' + verdict;
  const label = verdict.replace(/_/g, ' ');
  return `<span class="${cls} font-bold">${label}</span>`;
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
    pnlEl.innerHTML = `<span class="${data.avg_pnl_closed >= 0 ? 'pnl-pos' : 'pnl-neg'}">${sign}${data.avg_pnl_closed.toFixed(1)}%</span>`;
  } else {
    pnlEl.textContent = '—';
  }
}

// ── render: scores table ────────────────────────────────────────

function renderScores(scores) {
  const tbody = document.getElementById('scores-table');
  if (!scores || !scores.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="p-3 text-slate-500 text-center">No scored candidates yet</td></tr>';
    return;
  }
  tbody.innerHTML = scores.map(s => `
    <tr class="clickable border-b border-slate-700/50" onclick="showDetail(${s.id})">
      <td class="p-2 text-slate-400">${formatTime(s.created_at)}</td>
      <td class="p-2"><code>${escapeHtml(s.short_token)}</code></td>
      <td class="p-2 text-right font-bold verdict-${s.verdict}">${s.runner_score.toFixed(1)}</td>
      <td class="p-2">${verdictBadge(s.verdict)}</td>
      <td class="p-2 text-slate-300 truncate max-w-[200px]">${escapeHtml(s.top_reason)}</td>
      <td class="p-2 text-slate-400 truncate max-w-[180px]">${escapeHtml(s.top_caution)}</td>
      <td class="p-2 text-center">${s.has_position ? '<span class="text-green-400">Y</span>' : ''}</td>
      <td class="p-2 text-center">${s.short_circuited ? '<span class="text-red-400">Y</span>' : ''}</td>
    </tr>
  `).join('');
}

// ── render: positions table ─────────────────────────────────────

function renderPositions(positions) {
  const tbody = document.getElementById('positions-table');
  if (!positions || !positions.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="p-3 text-slate-500 text-center">No paper positions yet</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(p => `
    <tr class="border-b border-slate-700/50">
      <td class="p-2"><code>${escapeHtml(p.symbol || p.short_token)}</code></td>
      <td class="p-2 text-slate-400">${formatTime(p.signal_time)}</td>
      <td class="p-2">${verdictBadge(p.verdict)}</td>
      <td class="p-2 text-right">${p.entry_price_usd != null ? '$' + p.entry_price_usd.toPrecision(3) : '—'}</td>
      <td class="p-2 text-right">${formatPnl(p.pnl_5m)}</td>
      <td class="p-2 text-right">${formatPnl(p.pnl_30m)}</td>
      <td class="p-2 text-right">${formatPnl(p.pnl_1h)}</td>
      <td class="p-2 text-right">${formatPnl(p.pnl_4h)}</td>
      <td class="p-2 text-right">${formatPnl(p.pnl_24h)}</td>
      <td class="p-2 text-right">${formatPnl(p.mfe)}</td>
      <td class="p-2 text-right">${formatPnl(p.mae)}</td>
      <td class="p-2 text-center"><span class="${p.status === 'open' ? 'text-yellow-400' : 'text-slate-400'}">${p.status}</span></td>
    </tr>
  `).join('');
}

// ── detail panel ────────────────────────────────────────────────

async function showDetail(scoreId) {
  currentDetailId = scoreId;
  const panel = document.getElementById('detail-panel');
  const content = document.getElementById('detail-content');
  const title = document.getElementById('detail-title');

  panel.classList.remove('hidden');
  content.innerHTML = '<p class="text-slate-500">Loading...</p>';

  const d = await fetchJSON(`/api/scores/${scoreId}`);
  if (!d) {
    content.innerHTML = '<p class="text-red-400">Failed to load detail</p>';
    return;
  }

  title.innerHTML = `${escapeHtml(d.short_token)} — ${verdictBadge(d.verdict)} (${d.runner_score.toFixed(1)})`;

  let html = '<div class="grid grid-cols-2 gap-4">';

  // Left: dimensions
  html += '<div>';
  html += '<h4 class="text-xs font-bold text-slate-400 mb-2">Score Breakdown</h4>';
  const dimOrder = ['wallet_quality', 'cluster_quality', 'entry_quality', 'holder_quality', 'rug_risk', 'follow_through', 'narrative'];
  for (const key of dimOrder) {
    const dim = (d.dimensions || {})[key];
    if (!dim) continue;
    const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const pct = Math.max(0, Math.min(100, dim.score));
    const color = dim.score >= 60 ? 'bg-green-600' : dim.score >= 40 ? 'bg-yellow-600' : 'bg-red-600';
    html += `
      <div class="mb-1.5">
        <div class="flex justify-between text-xs mb-0.5">
          <span class="text-slate-300">${label}</span>
          <span class="text-slate-400">${dim.score.toFixed(0)} (x${dim.weight.toFixed(2)} = ${dim.weighted.toFixed(1)})</span>
        </div>
        <div class="w-full bg-slate-700 rounded h-1.5">
          <div class="bar-fill ${color} rounded h-1.5" style="width:${pct}%"></div>
        </div>
      </div>
    `;
  }
  html += '</div>';

  // Right: reasons + cautions + meta
  html += '<div>';

  // Top 3 reasons
  html += '<h4 class="text-xs font-bold text-slate-400 mb-1">Top Reasons</h4>';
  html += '<ol class="text-xs text-slate-300 mb-3 list-decimal list-inside">';
  for (const r of (d.top_reasons || [])) {
    html += `<li>${escapeHtml(r.name)} ${r.score.toFixed(0)} (x${r.weight.toFixed(2)} = ${r.weighted.toFixed(1)})</li>`;
  }
  html += '</ol>';

  // Cautions
  html += '<h4 class="text-xs font-bold text-slate-400 mb-1">Cautions</h4>';
  html += '<ul class="text-xs text-slate-300 mb-3">';
  for (const c of (d.cautions || [])) {
    html += `<li class="${c === 'None' ? 'text-slate-500' : 'text-yellow-400'}">• ${escapeHtml(c)}</li>`;
  }
  html += '</ul>';

  // Raw scores
  html += '<h4 class="text-xs font-bold text-slate-400 mb-1">Raw Scores</h4>';
  html += `<div class="text-xs text-slate-300 mb-3">Rug Risk: ${d.raw_rug_risk ?? '—'} | Insider Risk: ${d.raw_insider_risk ?? '—'}</div>`;

  // Cluster
  html += '<h4 class="text-xs font-bold text-slate-400 mb-1">Cluster</h4>';
  const cl = d.cluster || {};
  const tiers = Array.isArray(cl.tier_counts) ? cl.tier_counts.join(', ') : JSON.stringify(cl.tier_counts || {});
  html += `<div class="text-xs text-slate-300 mb-3">${cl.wallet_count || 0} wallets, ${(cl.convergence_minutes || 0).toFixed(0)} min convergence, tiers: ${tiers}</div>`;

  // Version
  html += `<div class="text-xs text-slate-500 mb-3">v: ${escapeHtml(d.scoring_version)} | hash: ${escapeHtml(d.weights_hash)}</div>`;

  // Position milestones
  if (d.position) {
    const p = d.position;
    html += '<h4 class="text-xs font-bold text-slate-400 mb-1">Position</h4>';
    html += '<div class="text-xs text-slate-300 mb-1">';
    html += `Status: ${p.status} | Entry: $${(p.entry_price_usd || 0).toPrecision(3)} | Size: ${p.amount_sol} SOL`;
    html += '</div>';
    html += '<div class="text-xs text-slate-300 mb-1">';
    const milestones = ['5m', '30m', '1h', '4h', '24h'];
    const vals = milestones.map(m => {
      const v = p['pnl_' + m];
      return v != null ? `${m}: ${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : null;
    }).filter(Boolean);
    html += vals.join(' | ') || 'No milestones yet';
    html += '</div>';
    html += `<div class="text-xs text-slate-300">MFE: ${formatPnl(p.mfe)} | MAE: ${formatPnl(p.mae)}</div>`;
  }

  // Links
  html += '<div class="mt-3 text-xs">';
  html += `<a href="${d.links?.dexscreener || '#'}" target="_blank" class="text-blue-400 hover:underline mr-3">DexScreener</a>`;
  html += `<a href="${d.links?.solscan || '#'}" target="_blank" class="text-blue-400 hover:underline">Solscan</a>`;
  html += '</div>';

  html += '</div></div>';
  content.innerHTML = html;
}

function closeDetail() {
  document.getElementById('detail-panel').classList.add('hidden');
  currentDetailId = null;
}

// ── polling ─────────────────────────────────────────────────────

async function refreshAll() {
  const indicator = document.getElementById('status-indicator');
  try {
    const [stats, scores, positions] = await Promise.all([
      fetchJSON('/api/stats'),
      fetchJSON('/api/scores?limit=50'),
      fetchJSON('/api/positions?limit=50'),
    ]);
    renderStats(stats);
    renderScores(scores?.scores);
    renderPositions(positions?.positions);
    indicator.textContent = 'updated ' + new Date().toLocaleTimeString();
    indicator.className = 'text-xs text-green-600';
  } catch (e) {
    indicator.textContent = 'poll error';
    indicator.className = 'text-xs text-red-500';
  }
}

// Initial load + poll
refreshAll();
setInterval(refreshAll, POLL_INTERVAL);
