/* Admin panel — API + Chart.js (Universiteti AAB) */
(function () {
  'use strict';

  const DEPARTMENTS = [
    'Programim', 'Programim dhe AI', 'Programim dhe Multimedia',
    'Programim i Lojërave Kompjuterike', 'Siguri Kibernetike',
    'Specializim në Inxhinieri Softuerike',
  ];
  const CHART_COLORS = ['#C63F3F', '#C9A84C', '#A52D2D', '#6B1414', '#2D7A3A', '#1565C0', '#8A6A6A', '#E05555'];
  const charts = {};
  let currentPage = 'dashboard';

  function apiOrigin() {
    const origin = window.location.origin;
    if (origin && origin !== 'null' && !origin.startsWith('file:') && /:8000$/.test(origin)) {
      return origin;
    }
    return 'http://127.0.0.1:8000';
  }

  const API = apiOrigin() + '/api';
  let TOKEN = localStorage.getItem('admin_token') || localStorage.getItem('token');
  let allTezat = [];
  let dashboardData = null;
  let adminSettings = { plagiarism_threshold: 30 };
  let _tezatSyncedOnce = false;
  const chatHistory = [];

  function thresholdNum() {
    const t = Number(adminSettings.plagiarism_threshold);
    return Number.isFinite(t) && t > 0 ? t : 30;
  }

  function warnThreshold() {
    return Math.max(5, Math.round(thresholdNum() * 2 / 3));
  }

  function applySettingsLabels() {
    const th = thresholdNum();
    const warn = warnThreshold();
    const setTh = document.getElementById('set-threshold');
    if (setTh) setTh.value = th;
    const foot = document.getElementById('stat-footer-highrisk');
    if (foot) foot.textContent = 'mbi ' + th + '% ngjashmëri';
    const riskyTitle = document.getElementById('raporte-risky-title');
    if (riskyTitle) riskyTitle.textContent = 'Temat Problematike (>' + th + '%)';
  }

  async function loadAdminSettings() {
    try {
      const data = await apiFetch('/admin/settings');
      adminSettings = { ...adminSettings, ...(data.settings || {}) };
    } catch (e) { /* defaults */ }
    applySettingsLabels();
    const apiEl = document.getElementById('set-api');
    if (apiEl && adminSettings.api_endpoint) apiEl.value = adminSettings.api_endpoint;
    else if (apiEl && !apiEl.value) apiEl.value = API;
  }

  function apiError(detail) {
    if (!detail) return 'Gabim i panjohur';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(x => x.msg || x.message || JSON.stringify(x)).join(', ');
    return String(detail);
  }

  async function apiFetch(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (TOKEN) headers.Authorization = 'Bearer ' + TOKEN;
    if (options.body && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(options.body);
    }
    const res = await fetch(API + path, { ...options, headers });
    if (res.status === 401) { logout(); throw new Error('Sesioni skadoi.'); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(apiError(data.detail) || res.statusText);
    return data;
  }

  function logout() {
    ['admin_token', 'admin_user', 'token', 'user'].forEach(k => localStorage.removeItem(k));
    window.location.href = 'ballina.html';
  }
  window.logout = logout;

  function initials(name) {
    if (!name) return '?';
    return name.split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
  }

  function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escAttr(v) {
    return String(v ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;');
  }

  let _currentReportResultId = null;
  let _currentReportThesisId = null;
  let _lastAdminResult = null;
  const AUTO_PASS_THRESHOLD = 20;
  const MATCH_LABELS = {
    exact_copy: 'Kopjuar plotësisht',
    reordered: 'Rend i ndryshuar',
    partial: 'Pjesërisht e ngjashme',
    translated: 'Përkthim / parafrazë',
  };
  const MATCH_CLASS = {
    exact_copy: 'hl-exact',
    reordered: 'hl-reordered',
    partial: 'hl-partial',
    translated: 'hl-translated',
  };
  const MATCH_SHORT = {
    exact_copy: 'Kopjuar',
    reordered: 'Rend i ndryshuar',
    partial: 'E ngjashme',
    translated: 'Përkthyer',
  };

  function reportButtonHtml(t) {
    if (!t.result_id) return '';
    const pct = t.pct != null ? t.pct : 0;
    const thesisId = t.id || t.thesis_id || '';
    return '<button type="button" class="panel-action btn-admin-report" data-result-id="' + t.result_id +
      '" data-thesis-id="' + thesisId +
      '" data-name="' + escAttr(t.student) + '" data-title="' + escAttr(t.title) +
      '" data-pct="' + pct + '" data-status="' + escAttr(t.status) + '">Raporti</button>';
  }

  document.addEventListener('click', function (e) {
    const btn = e.target.closest('.btn-admin-report');
    if (!btn) return;
    e.preventDefault();
    window.showResult({
      resultId: Number(btn.dataset.resultId),
      thesisId: Number(btn.dataset.thesisId || 0) || null,
      name: btn.dataset.name || '—',
      title: btn.dataset.title || '',
      pct: Number(btn.dataset.pct || 0),
      status: btn.dataset.status || 'process',
    });
  });

  function pctColor(pct) {
    const th = thresholdNum();
    const warn = warnThreshold();
    if (pct > th) return { bar: '#C63F3F', txt: '#A52D2D' };
    if (pct > warn) return { bar: '#C9A84C', txt: '#7A5010' };
    return { bar: '#4CAF50', txt: '#1B5E20' };
  }

  function statusFromScore(pct, resultStatus) {
    if (resultStatus === 'pending' || pct === null || pct === undefined) return 'process';
    const th = thresholdNum();
    const warn = warnThreshold();
    if (pct > th) return 'danger';
    if (pct > warn) return 'warn';
    return 'ok';
  }

  function pctBadge(status) {
    const map = {
      ok: '<span class="badge badge-ok">Kaluar</span>',
      danger: '<span class="badge badge-danger">Lartë</span>',
      warn: '<span class="badge badge-warn">Mesatare</span>',
      process: '<span class="badge badge-process">Procesim</span>',
    };
    return map[status] || '';
  }

  function roleBadge(role) {
    const r = (role || '').toLowerCase();
    if (r === 'admin') return '<span class="badge badge-admin">Admin</span>';
    if (r === 'professor' || r === 'profesor') return '<span class="badge badge-prof">Profesor</span>';
    return '<span class="badge badge-process">Student</span>';
  }

  function destroyChart(id) {
    if (charts[id]) { charts[id].destroy(); delete charts[id]; }
  }

  function chartFont() {
    return { family: "'DM Sans', sans-serif", size: 11 };
  }

  function renderDeptBarChart(canvasId, depts) {
    destroyChart(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;
    const labels = depts.length ? depts.map(d => d.name.length > 22 ? d.name.slice(0, 20) + '…' : d.name) : DEPARTMENTS.map(d => d.slice(0, 18));
    const data = depts.length ? depts.map(d => d.count) : DEPARTMENTS.map(() => 0);
    charts[canvasId] = new Chart(el, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Tema',
          data,
          backgroundColor: CHART_COLORS.map(c => c + 'CC'),
          borderColor: CHART_COLORS,
          borderWidth: 1,
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { backgroundColor: '#2D0808' } },
        scales: {
          x: { grid: { display: false }, ticks: { font: chartFont(), maxRotation: 45 } },
          y: { beginAtZero: true, ticks: { stepSize: 1, font: chartFont() }, grid: { color: 'rgba(0,0,0,.05)' } },
        },
      },
    });
  }

  function renderRiskDonut(canvasId, stats, items) {
    destroyChart(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;
    const th = thresholdNum();
    const warn = warnThreshold();
    let ok = 0, warnC = 0, danger = 0, pending = stats?.processing || 0;
    if (items?.length) {
      items.forEach(t => {
        if (t.pct === null) pending++;
        else if (t.pct > th) danger++;
        else if (t.pct > warn) warnC++;
        else ok++;
      });
    } else {
      ok = Math.max(0, (stats?.analyzed_theses || 0) - (stats?.high_risk || 0));
      danger = stats?.high_risk || 0;
    }
    charts[canvasId] = new Chart(el, {
      type: 'doughnut',
      data: {
        labels: [
          'Kaluar (≤' + warn + '%)',
          'Mesatare (' + warn + '–' + th + '%)',
          'Lartë (>' + th + '%)',
          'Në proces',
        ],
        datasets: [{
          data: [ok, warnC, danger, pending],
          backgroundColor: ['#4CAF50', '#C9A84C', '#C63F3F', '#1565C0'],
          borderWidth: 0,
          hoverOffset: 8,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: '68%',
        plugins: {
          legend: { position: 'bottom', labels: { padding: 14, font: chartFont(), usePointStyle: true } },
        },
      },
    });
  }

  function renderTrendChart(canvasId, monthly) {
    destroyChart(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;
    charts[canvasId] = new Chart(el, {
      type: 'line',
      data: {
        labels: monthly.labels,
        datasets: [{
          label: 'Tema të ngarkuara',
          data: monthly.counts,
          borderColor: '#C63F3F',
          backgroundColor: 'rgba(198,63,63,.12)',
          fill: true,
          tension: 0.4,
          pointRadius: 5,
          pointBackgroundColor: '#C63F3F',
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { font: chartFont() } },
          y: { beginAtZero: true, ticks: { stepSize: 1, font: chartFont() }, grid: { color: 'rgba(0,0,0,.05)' } },
        },
      },
    });
  }

  function buildMonthlyTrend(items) {
    const months = [];
    const now = new Date();
    for (let i = 5; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
      months.push({ key: d.getFullYear() + '-' + d.getMonth(), label: d.toLocaleDateString('sq', { month: 'short' }) });
    }
    const counts = months.map(() => 0);
    items.forEach(t => {
      const raw = t.created_at || t.year;
      if (!raw) return;
      let d;
      if (String(raw).includes('-')) d = new Date(raw);
      else d = new Date(parseInt(raw, 10), 0, 1);
      const key = d.getFullYear() + '-' + d.getMonth();
      const idx = months.findIndex(m => m.key === key);
      if (idx >= 0) counts[idx]++;
    });
    return { labels: months.map(m => m.label), counts };
  }

  function renderChips(containerId, chips) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = chips.map(c =>
      '<div class="chip-stat' + (c.accent ? ' accent' : '') + '">' + c.label + ' <strong>' + c.value + '</strong></div>'
    ).join('');
  }

  async function loadDepartmentSelects() {
    let depts = DEPARTMENTS;
    try {
      const data = await fetch(apiOrigin() + '/api/departments').then(r => r.json());
      if (data.departments?.length) depts = data.departments;
    } catch (e) { /* fallback */ }
    const sel = document.getElementById('filter-dept');
    if (sel) {
      sel.innerHTML = '<option value="">Të gjitha departamentet</option>' +
        depts.map(d => '<option>' + escapeHtml(d) + '</option>').join('');
    }
  }

  const breadcrumbs = {
    dashboard: 'Dashboard', agent: 'AI Agent', tezat: 'Temat e Diplomës',
    raporte: 'Raporte & Statistika', users: 'Përdoruesit', audit: 'Audit',
    settings: 'Cilësimet',
  };

  window.showPage = function (id, btn) {
    currentPage = id;
    document.querySelectorAll('.page-section, .page-agent-section').forEach(el => el.classList.remove('active'));
    document.getElementById('pg-' + id)?.classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    if (btn) btn.classList.add('active');
    document.getElementById('breadcrumb-current').textContent = breadcrumbs[id] || id;
    if (id === 'tezat') loadTezat({ syncTemat: !_tezatSyncedOnce });
    if (id === 'dashboard') loadDashboard();
    if (id === 'raporte') loadRaporte();
    if (id === 'users') loadUsers();
    if (id === 'audit') loadAudit();
    if (id === 'settings') loadSettings();
    setTimeout(() => Object.values(charts).forEach(c => c?.resize?.()), 120);
  };

  window.refreshCurrentPage = function () {
    showPage(currentPage, document.querySelector('.nav-item.active'));
  };

  async function loadDashboard() {
    try {
      dashboardData = await apiFetch('/admin/dashboard');
      if (dashboardData.settings) {
        adminSettings = { ...adminSettings, ...dashboardData.settings };
        applySettingsLabels();
      }
      if (dashboardData.stats?.plagiarism_threshold != null) {
        adminSettings.plagiarism_threshold = dashboardData.stats.plagiarism_threshold;
        applySettingsLabels();
      }
      const s = dashboardData.stats || {};
      const db = dashboardData.database || {};

      const cards = document.querySelectorAll('#pg-dashboard .stat-value');
      if (cards[0]) cards[0].textContent = s.total_theses ?? 0;
      if (cards[1]) cards[1].textContent = s.analyzed_theses ?? 0;
      if (cards[2]) cards[2].textContent = s.high_risk ?? 0;
      if (cards[3]) cards[3].textContent = s.processing ?? 0;

      const footers = document.querySelectorAll('#pg-dashboard .stat-footer');
      if (footers[1]) {
        footers[1].textContent = s.total_theses
          ? Math.round((s.analyzed_theses / s.total_theses) * 1000) / 10 + '% e totalit'
          : '0% e totalit';
      }

      renderChips('dash-chips', [
        { label: 'Përdorues', value: s.total_users ?? 0 },
        { label: 'Profesorë', value: s.professors ?? 0 },
        { label: 'Studentë', value: s.students ?? 0, accent: true },
      ]);

      const dbBadge = document.getElementById('db-status-badge');
      const dbText = document.getElementById('db-status-text');
      if (dbBadge && dbText) {
        if (db.ok) { dbBadge.style.background = '#E8F5E9'; dbBadge.style.color = '#2D7A3A'; dbText.textContent = 'Databaza Aktive'; }
        else { dbBadge.style.background = '#FDEAEA'; dbBadge.style.color = '#C63F3F'; dbText.textContent = 'Databaza Offline'; }
      }

      const depts = dashboardData.departments || [];
      renderDeptBarChart('chart-dash-dept', depts);

      if (!allTezat.length) {
        try {
          const td = await apiFetch('/admin/theses?limit=200');
          allTezat = mapTheses(td.items || []);
        } catch (e) { /* ignore */ }
      }
      renderRiskDonut('chart-dash-risk', s, allTezat);
      renderDashboardTable(dashboardData.recent_results || []);

      const agentInfo = document.getElementById('agent-info');
      if (agentInfo) {
        agentInfo.textContent = 'Asistent me akses në ' + (s.total_theses || 0) +
          ' tema të enkriptuara. Analizon plagjiaturë, statistika dhe raporte — pa ekspozuar përmbajtje.';
      }
    } catch (e) {
      console.error(e);
    }
  }

  function mapTheses(items) {
    return items.map(t => ({
      id: t.id, student: t.student_name || '—', title: t.title,
      dept: t.department || '—', year: t.year ? String(t.year) : '—',
      pct: t.plagiarism_score != null ? Math.round(t.plagiarism_score) : null,
      status: statusFromScore(t.plagiarism_score, t.result_status || t.status),
      result_id: t.result_id, created_at: t.created_at,
      file_type: t.file_type || 'pdf',
      file_name: t.file_name || t.title || 'tema',
      has_file: t.has_file !== false,
    }));
  }

  function renderDashboardTable(rows) {
    const tbody = document.getElementById('dashboard-table');
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state"><div class="empty-state-illustration">📊</div>Nuk ka analiza ende</div></td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(r => {
      const pct = r.overall_score != null ? Math.round(r.overall_score) : null;
      const st = statusFromScore(pct, r.status);
      const name = r.student_name || r.title || '—';
      const pctCell = pct !== null
        ? (() => { const c = pctColor(pct); return '<div class="pct-wrap"><div class="pct-track"><div class="pct-fill" style="width:' + pct + '%;background:' + c.bar + '"></div></div><span class="pct-label" style="color:' + c.txt + '">' + pct + '%</span></div>'; })()
        : '<span style="color:var(--muted)">—</span>';
      const action = r.id ? reportButtonHtml({
        result_id: r.id,
        id: r.thesis_id,
        thesis_id: r.thesis_id,
        student: name,
        title: r.title || name,
        pct: pct,
        status: st,
      }) : '';
      return '<tr><td><div class="user-avatar-cell"><div class="user-avatar-small">' + initials(name) + '</div><span>' + escapeHtml(name) + '</span></div></td><td>' + escapeHtml(r.department || '—') + '</td><td>' + pctCell + '</td><td>' + pctBadge(st) + '</td><td>' + action + '</td></tr>';
    }).join('');
  }

  async function loadTezat(opts = {}) {
    try {
      const search = document.getElementById('search-input')?.value || '';
      const dept = document.getElementById('filter-dept')?.value || '';
      const qs = new URLSearchParams();
      qs.set('limit', '500');
      if (opts.syncTemat === true) qs.set('sync_temat', '1');
      else qs.set('sync_temat', '0');
      if (opts.syncTemat === true) _tezatSyncedOnce = true;
      if (search) qs.set('search', search);
      if (dept) qs.set('department', dept);
      const data = await apiFetch('/admin/theses?' + qs.toString());
      allTezat = mapTheses(data.items || []);
      const ok = allTezat.filter(t => t.status === 'ok').length;
      const risk = allTezat.filter(t => t.status === 'danger').length;
      renderChips('tezat-chips', [
        { label: 'Gjithsej', value: allTezat.length },
        { label: 'Kaluar', value: ok },
        { label: 'Rrezik', value: risk, accent: true },
      ]);
      renderTezat();
    } catch (e) { console.error(e); }
  }

  window.renderTezat = function () {
    const search = (document.getElementById('search-input')?.value || '').toLowerCase();
    const dept = document.getElementById('filter-dept')?.value || '';
    const filtered = allTezat.filter(t => {
      const m1 = !search || t.student.toLowerCase().includes(search) || t.title.toLowerCase().includes(search);
      const m2 = !dept || t.dept === dept;
      return m1 && m2;
    });
    const tbody = document.getElementById('tezat-tbody');
    if (!tbody) return;
    if (!filtered.length) {
      tbody.innerHTML = '<tr><td colspan="8"><div class="empty-state"><div class="empty-state-illustration">📄</div>Nuk u gjet asnjë temë</div></td></tr>';
      return;
    }
    tbody.innerHTML = filtered.map(t => {
      const c = t.pct !== null ? pctColor(t.pct) : null;
      const pctCell = t.pct !== null
        ? '<div class="pct-wrap"><div class="pct-track"><div class="pct-fill" style="width:' + t.pct + '%;background:' + c.bar + '"></div></div><span class="pct-label" style="color:' + c.txt + '">' + t.pct + '%</span></div>'
        : '<span style="color:var(--muted)">—</span>';
      const reportBtn = reportButtonHtml(t);
      const openBtn = t.has_file
        ? '<button type="button" class="btn-tezat btn-tezat-open" onclick="window.openAdminThesis(' + t.id + ')">Hap</button>'
        : '';
      const delBtn = '<button type="button" class="btn-tezat btn-tezat-del" onclick="window.deleteAdminThesis(' + t.id + ')">Fshij</button>';
      const actions = '<div class="tezat-actions">' + openBtn + delBtn + reportBtn + '</div>';
      return '<tr><td style="color:var(--muted)">' + t.id + '</td><td><div class="user-avatar-cell"><div class="user-avatar-small">' + initials(t.student) + '</div><span style="font-weight:500">' + escapeHtml(t.student) + '</span></div></td><td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(t.title) + '">' + escapeHtml(t.title) + '</td><td>' + escapeHtml(t.dept) + '</td><td>' + t.year + '</td><td>' + pctCell + '</td><td>' + pctBadge(t.status) + '</td><td>' + actions + '</td></tr>';
    }).join('');
  };

  window.filterTezat = function () { loadTezat(); };

  let _viewerState = { thesisId: null, fileName: null, blobUrl: null };

  function closeThesisViewer() {
    const backdrop = document.getElementById('thesis-viewer-backdrop');
    if (backdrop) backdrop.classList.remove('open');
    if (_viewerState.blobUrl) {
      URL.revokeObjectURL(_viewerState.blobUrl);
      _viewerState.blobUrl = null;
    }
    const frame = document.getElementById('thesis-viewer-frame');
    if (frame) { frame.removeAttribute('src'); frame.style.display = 'none'; }
    const pre = document.getElementById('thesis-viewer-text');
    if (pre) { pre.textContent = ''; pre.style.display = 'none'; }
    const fallback = document.getElementById('thesis-viewer-fallback');
    if (fallback) { fallback.innerHTML = ''; fallback.style.display = 'none'; }
    const loading = document.getElementById('thesis-viewer-loading');
    if (loading) loading.style.display = 'none';
  }
  window.closeThesisViewer = closeThesisViewer;

  window.downloadCurrentThesis = async function () {
    if (!_viewerState.thesisId) return;
    try {
      const r = await fetch(API + '/theses/' + _viewerState.thesisId + '/file', {
        headers: { Authorization: 'Bearer ' + TOKEN },
      });
      if (!r.ok) throw new Error('Skedari nuk u shkarkua');
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = _viewerState.fileName || 'tema';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e.message || 'Gabim gjatë shkarkimit');
    }
  };

  window.openAdminThesis = async function (thesisId) {
    let row = allTezat.find(t => Number(t.id) === Number(thesisId));
    if (!row) {
      try {
        const t = await apiFetch('/theses/' + thesisId);
        row = {
          id: t.id,
          title: t.title,
          file_type: t.file_type || 'pdf',
          file_name: t.file_name || t.title || 'tema',
          has_file: true,
        };
      } catch (e) {
        alert(e.message || 'Tema nuk u gjet. Rifresko faqen.');
        return;
      }
    }
    const fileType = row.file_type || 'pdf';
    const fileName = row.file_name || row.title || 'tema';
    const title = row.title || 'Tema';
    closeThesisViewer();
    const backdrop = document.getElementById('thesis-viewer-backdrop');
    const loading = document.getElementById('thesis-viewer-loading');
    backdrop.classList.add('open');
    loading.style.display = 'flex';
    document.getElementById('thesis-viewer-title').textContent = title || 'Tema';
    document.getElementById('thesis-viewer-meta').textContent = fileName || '';
    _viewerState = { thesisId, fileName: fileName || 'tema', blobUrl: null };

    const type = String(fileType || 'pdf').toLowerCase().replace(/^.*\./, '');

    try {
      const r = await fetch(API + '/theses/' + thesisId + '/file?inline=1', {
        headers: { Authorization: 'Bearer ' + TOKEN },
      });
      if (!r.ok) {
        let detail = 'Skedari nuk u hap';
        try { detail = (await r.json()).detail || detail; } catch {}
        throw new Error(detail);
      }

      loading.style.display = 'none';

      if (type === 'txt') {
        const text = await r.text();
        const pre = document.getElementById('thesis-viewer-text');
        pre.textContent = text || '(Skedari është bosh)';
        pre.style.display = 'block';
        return;
      }

      const mimeMap = { pdf: 'application/pdf', docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' };
      const headerType = (r.headers.get('content-type') || '').split(';')[0].trim();
      const mime = headerType && headerType !== 'application/octet-stream' ? headerType : (mimeMap[type] || 'application/octet-stream');
      const raw = await r.blob();
      const blob = raw.type && raw.type !== 'application/octet-stream' ? raw : new Blob([raw], { type: mime });
      const url = URL.createObjectURL(blob);
      _viewerState.blobUrl = url;

      if (type === 'pdf') {
        const frame = document.getElementById('thesis-viewer-frame');
        frame.src = url;
        frame.style.display = 'block';
        return;
      }

      const fallback = document.getElementById('thesis-viewer-fallback');
      fallback.innerHTML = '<p>Skedari ' + escapeHtml(type.toUpperCase()) + ' nuk shfaqet direkt në shfletues.</p><button type="button" class="btn-tezat btn-tezat-open" onclick="downloadCurrentThesis()">Shkarko skedarin</button>';
      fallback.style.display = 'flex';
    } catch (e) {
      closeThesisViewer();
      alert(e.message || 'Gabim gjatë hapjes së temës');
    }
  };

  let _deletingThesis = false;

  window.deleteAdminThesis = async function (thesisId) {
    const id = Number(thesisId);
    if (!id || _deletingThesis) return;

    const row = allTezat.find(t => Number(t.id) === id);
    const title = row?.title || '';
    const label = title ? '«' + title + '»' : 'këtë temë';
    if (!confirm('A je i sigurt që dëshiron ta fshish ' + label + '?\n\nVeprimi nuk mund të kthehet.')) return;

    _deletingThesis = true;
    const delButtons = document.querySelectorAll('.btn-tezat-del');
    delButtons.forEach(btn => { btn.disabled = true; });

    try {
      TOKEN = localStorage.getItem('admin_token') || localStorage.getItem('token');
      const res = await fetch(API + '/admin/theses/' + id, {
        method: 'DELETE',
        headers: TOKEN ? { Authorization: 'Bearer ' + TOKEN } : {},
      });
      if (res.status === 401) {
        logout();
        throw new Error('Sesioni skadoi.');
      }
      let data = {};
      try { data = await res.json(); } catch { /* empty body ok */ }
      if (!res.ok) {
        throw new Error(apiError(data.detail) || data.message || res.statusText || 'Tema nuk u fshi');
      }

      await loadTezat();
      if (dashboardData?.stats) {
        dashboardData.stats.total_theses = allTezat.length;
      }
    } catch (e) {
      console.error('deleteAdminThesis:', e);
      alert(e.message || 'Tema nuk u fshi. Provo përsëri.');
    } finally {
      _deletingThesis = false;
      delButtons.forEach(btn => { btn.disabled = false; });
    }
  };

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeThesisViewer();
  });

  async function loadRaporte() {
    if (!dashboardData) await loadDashboard();
    const depts = dashboardData?.departments || [];
    const s = dashboardData?.stats || {};
    renderDeptBarChart('chart-raporte-dept', depts);

    try {
      const data = await apiFetch('/admin/theses?limit=200');
      allTezat = mapTheses(data.items || []);
      renderTrendChart('chart-raporte-trend', buildMonthlyTrend(data.items || []));

      const risky = allTezat.filter(t => t.pct !== null && t.pct > thresholdNum()).sort((a, b) => b.pct - a.pct);
      const badge = document.getElementById('raporte-risk-badge');
      if (badge) badge.textContent = risky.length + ' gjithsej';

      const tbody = document.getElementById('raporte-risky-tbody');
      if (tbody) {
        if (!risky.length) {
          tbody.innerHTML = '<tr><td colspan="4"><div class="empty-state">Nuk ka tema problematike 🎉</div></td></tr>';
        } else {
          tbody.innerHTML = risky.slice(0, 10).map(t =>
            '<tr><td>' + escapeHtml(t.student) + '</td><td>' + escapeHtml(t.dept) + '</td><td><span style="color:var(--red);font-weight:600">' + t.pct + '%</span></td><td>' + reportButtonHtml(t) + '</td></tr>'
          ).join('');
        }
      }

      const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
      set('metric-users', s.total_users ?? 0);
      set('metric-profs', s.professors ?? 0);
      set('metric-students', s.students ?? 0);
      set('metric-analyzed', s.analyzed_theses ?? 0);

      const scored = allTezat.filter(t => t.pct !== null);
      const avg = scored.length ? Math.round(scored.reduce((a, t) => a + t.pct, 0) / scored.length) : 0;
      const avgEl = document.getElementById('avg-similarity');
      if (avgEl) avgEl.textContent = scored.length ? avg + '%' : '—';
      const gauge = document.getElementById('risk-gauge');
      if (gauge) gauge.style.setProperty('--needle-deg', (-90 + Math.min(avg, 100) * 1.8) + 'deg');
    } catch (e) { console.error(e); }
  }

  async function loadUsers() {
    try {
      const users = await apiFetch('/auth/users');
      const admins = users.filter(u => u.role === 'admin').length;
      const profs = users.filter(u => u.role === 'professor').length;
      const studs = users.filter(u => u.role === 'student').length;
      renderChips('users-chips', [
        { label: 'Gjithsej', value: users.length },
        { label: 'Profesorë', value: profs },
        { label: 'Studentë', value: studs, accent: true },
      ]);
      const tbody = document.getElementById('users-tbody');
      if (!tbody) return;
      if (!users.length) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty-state">Nuk ka përdorues</div></td></tr>';
        return;
      }
      tbody.innerHTML = users.map(u => {
        const name = u.full_name || u.username;
        const login = u.last_login ? new Date(u.last_login).toLocaleDateString('sq', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
        const active = u.is_active !== false;
        const delBtn = u.role === 'admin'
          ? ''
          : '<button type="button" class="btn-tezat btn-tezat-del" onclick="window.deleteAdminUser(' + u.id + ',\'' + escAttr(name) + '\')">Fshij</button>';
        return '<tr><td><div class="user-avatar-cell"><div class="user-avatar-small">' + initials(name) + '</div><span style="font-weight:500">' + escapeHtml(name) + '</span></div></td><td>' + escapeHtml(u.email || '—') + '</td><td>' + roleBadge(u.role) + '</td><td>' + escapeHtml(u.department || '—') + '</td><td>' + login + '</td><td><span class="badge badge-' + (active ? 'ok' : 'danger') + '">' + (active ? 'Aktiv' : 'Joaktiv') + '</span></td><td>' + delBtn + '</td></tr>';
      }).join('');
    } catch (e) { console.error(e); }
  }

  async function loadAudit() {
    const tbody = document.getElementById('audit-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="7" class="empty-row">Duke ngarkuar...</td></tr>';
    try {
      const role = document.getElementById('audit-role-filter')?.value || '';
      const qs = role ? ('?role=' + encodeURIComponent(role) + '&limit=150') : '?limit=150';
      const data = await apiFetch('/admin/audit' + qs);
      const items = data.items || [];
      if (!items.length) {
        tbody.innerHTML = '<tr><td colspan="7"><div class="empty-state">Nuk ka ende veprime të regjistruara</div></td></tr>';
        return;
      }
      tbody.innerHTML = items.map(r => {
        const when = r.created_at ? new Date(r.created_at).toLocaleString('sq') : '—';
        return '<tr>'
          + '<td>' + escapeHtml(when) + '</td>'
          + '<td>' + escapeHtml(r.username || ('#' + (r.user_id || '—'))) + '</td>'
          + '<td>' + escapeHtml(r.role || '—') + '</td>'
          + '<td>' + escapeHtml(r.action || '—') + '</td>'
          + '<td>' + escapeHtml(r.method || '—') + '</td>'
          + '<td style="max-width:280px;overflow:hidden;text-overflow:ellipsis">' + escapeHtml(r.path || '—') + '</td>'
          + '<td>' + escapeHtml(r.ip_address || '—') + '</td>'
          + '</tr>';
      }).join('');
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-row">' + escapeHtml(e.message || 'Gabim') + '</td></tr>';
    }
  }
  window.loadAudit = loadAudit;

  window.deleteAdminUser = async function (userId, name) {
    const id = Number(userId);
    if (!id) return;
    const label = name ? '«' + name + '»' : 'këtë përdorues';
    if (!confirm('A je i sigurt që dëshiron ta fshish ' + label + '?\n\nVeprimi nuk mund të kthehet.')) return;
    try {
      await apiFetch('/auth/users/' + id, { method: 'DELETE' });
      await loadUsers();
      if (dashboardData?.stats) {
        dashboardData.stats.total_users = Math.max(0, (dashboardData.stats.total_users || 1) - 1);
      }
    } catch (e) {
      alert(e.message || 'Përdoruesi nuk u fshi');
    }
  };

  async function loadSettings() {
    await loadAdminSettings();
  }

  function scoreColor(pct) {
    return pct >= 50 ? 'var(--red)' : pct >= 25 ? 'var(--orange)' : 'var(--green)';
  }
  function highlightStats(flagged) {
    return {
      exact: flagged.filter(l => l.match_type === 'exact_copy').length,
      reordered: flagged.filter(l => l.match_type === 'reordered').length,
      partial: flagged.filter(l => l.match_type === 'partial').length,
      translated: flagged.filter(l => l.match_type === 'translated').length,
      total: flagged.length,
    };
  }
  function buildTypeBadge(matchType) {
    const cls = MATCH_CLASS[matchType] || 'hl-partial';
    const label = MATCH_SHORT[matchType] || MATCH_LABELS[matchType] || '';
    return '<span class="hl-type-badge ' + cls + '">' + label + '</span>';
  }
  function buildHighlightLegend() {
    return '<div class="hl-legend-row">' +
      '<span class="hl-legend-pill hl-exact"><span class="hl-dot"></span>Kopjuar</span>' +
      '<span class="hl-legend-pill hl-reordered"><span class="hl-dot"></span>Rend i ndryshuar</span>' +
      '<span class="hl-legend-pill hl-partial"><span class="hl-dot"></span>E ngjashme</span>' +
      '<span class="hl-legend-pill hl-translated"><span class="hl-dot"></span>Përkthyer/parafrazë</span>' +
      '</div>';
  }
  function buildStatsBar(stats) {
    return '<div class="hl-stats-bar">' +
      '<div class="hl-stat hl-stat-exact"><span class="hl-stat-num">' + (stats.exact || 0) + '</span><span class="hl-stat-lbl">Kopjuar</span></div>' +
      '<div class="hl-stat hl-stat-reordered"><span class="hl-stat-num">' + (stats.reordered || 0) + '</span><span class="hl-stat-lbl">Rend i ndryshuar</span></div>' +
      '<div class="hl-stat hl-stat-partial"><span class="hl-stat-num">' + (stats.partial || 0) + '</span><span class="hl-stat-lbl">E ngjashme</span></div>' +
      '<div class="hl-stat hl-stat-translated"><span class="hl-stat-num">' + (stats.translated || 0) + '</span><span class="hl-stat-lbl">Përkthyer</span></div>' +
      '<div class="hl-stat hl-stat-total"><span class="hl-stat-num">' + (stats.total || 0) + '</span><span class="hl-stat-lbl">Gjithsej</span></div>' +
      '</div>';
  }
  function buildCompactHighlightItem(line) {
    const cls = MATCH_CLASS[line.match_type] || 'hl-partial';
    const sim = Math.round(line.similarity || 0);
    const text = line.text || '';
    return '<div class="hl-result-item ' + cls + '">' +
      '<div class="hl-result-item-top">' +
      '<span class="hl-line-num">' + (line.line_number != null ? line.line_number : '') + '</span>' +
      buildTypeBadge(line.match_type) +
      '<span class="hl-sim-pill ' + cls + '">' + sim + '%</span>' +
      '</div>' +
      '<div class="hl-result-item-text">' + escapeHtml(text) + '</div>' +
      (line.source_text
        ? '<div class="hl-source-card ' + cls + '" style="margin:10px 0 0"><div class="hl-source-card-label">Teksti burimor</div><div class="hl-source-card-text">' +
          escapeHtml(line.source_text.slice(0, 300)) + (line.source_text.length > 300 ? '…' : '') + '</div></div>'
        : '') +
      '</div>';
  }
  function parseThesisIdFromMatch(m) {
    if (m.thesis_id) return Number(m.thesis_id);
    const src = String(m.source || m.source_title || '');
    const hit = src.match(/#?(\d+)/);
    return hit ? Number(hit[1]) : null;
  }
  function toggleWebSource(id) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle('open');
  }
  window.toggleWebSource = toggleWebSource;

  function renderMatchStatistics(d) {
    const panel = document.getElementById('res-match-stats');
    if (!panel) return;
    const ms = d.match_statistics;
    if (!ms || !ms.matched_lines) {
      panel.style.display = 'none';
      panel.innerHTML = '';
      return;
    }
    panel.style.display = 'block';
    panel.innerHTML =
      '<div class="turnitin-stats-head">Përmbledhje e ngjashmërisë (si Turnitin)</div>' +
      '<div class="turnitin-stats-grid">' +
      '<div class="tstat"><span class="tstat-num">' + (ms.matched_words || 0) + '</span><span class="tstat-lbl">Fjalë të përputhura</span><span class="tstat-sub">' + (ms.matched_word_pct || 0) + '% e dokumentit</span></div>' +
      '<div class="tstat"><span class="tstat-num">' + (ms.matched_lines || 0) + '</span><span class="tstat-lbl">Rreshta</span><span class="tstat-sub">' + (ms.matched_line_pct || 0) + '% · ' + (ms.total_lines || 0) + ' gjithsej</span></div>' +
      '<div class="tstat"><span class="tstat-num">' + (ms.matched_paragraphs || 0) + '</span><span class="tstat-lbl">Paragrafë</span><span class="tstat-sub">' + (ms.total_paragraphs || 0) + ' gjithsej</span></div>' +
      '<div class="tstat"><span class="tstat-num">' + (ms.matched_sentences || 0) + '</span><span class="tstat-lbl">Fjali</span><span class="tstat-sub">' + (ms.total_sentences || 0) + ' gjithsej</span></div>' +
      '</div>' + buildHighlightLegend();
  }

  function renderSourceDocuments(d) {
    const panel = document.getElementById('res-source-docs');
    if (!panel) return;
    const docs = d.source_documents || [];
    if (!docs.length) {
      panel.style.display = 'none';
      panel.innerHTML = '';
      return;
    }
    panel.style.display = 'block';
    panel.innerHTML =
      '<div class="turnitin-sources-head">Dokumentet burimore</div>' +
      '<div class="turnitin-sources-table-wrap"><table class="turnitin-sources-table">' +
      '<thead><tr><th>Burimi</th><th>Ngjashmëria</th><th>Rreshta</th><th>Paragrafë</th><th>Fjalë</th><th></th></tr></thead><tbody>' +
      docs.map(doc => {
        const pct = Math.round(doc.similarity || 0);
        const title = doc.source_type === 'web' && doc.source_url
          ? '<a href="' + escAttr(doc.source_url) + '" target="_blank" rel="noopener">' + escapeHtml(doc.source_title) + ' ↗</a>'
          : escapeHtml(doc.source_title || 'Burim');
        const openBtn = doc.thesis_id
          ? '<button type="button" class="btn-ghost btn-xs" onclick="openAdminThesis(' + doc.thesis_id + ')">Hap</button>'
          : (doc.source_url
            ? '<a class="btn-ghost btn-xs" href="' + escAttr(doc.source_url) + '" target="_blank" rel="noopener">Hap</a>'
            : '');
        return '<tr><td>' + title + '</td><td><span class="source-pct" style="color:' + scoreColor(pct) + '">' + pct +
          '%</span></td><td>' + (doc.line_count || 0) + '</td><td>' + (doc.paragraph_count || 0) +
          '</td><td>' + (doc.matched_words || 0) + '</td><td>' + openBtn + '</td></tr>';
      }).join('') +
      '</tbody></table></div>';
  }

  function renderResultHighlights(d) {
    const panel = document.getElementById('res-highlight-preview');
    if (!panel) return;
    const flagged = (d.line_highlights || []).filter(l => l.match_type);
    if (!flagged.length) {
      panel.style.display = 'none';
      panel.innerHTML = '';
      return;
    }
    const stats = highlightStats(flagged);
    panel.style.display = 'block';
    panel.innerHTML =
      '<div class="hl-result-head"><div><div class="hl-result-title">Rreshtat e identifikuar</div>' +
      '<div class="hl-result-sub">' + stats.total + ' rreshta me përputhje të dyshimta</div></div>' +
      buildStatsBar(stats) + '</div>' + buildHighlightLegend() +
      '<div class="hl-result-list">' + flagged.slice(0, 5).map(line => {
        const cls = MATCH_CLASS[line.match_type] || 'hl-partial';
        const sim = Math.round(line.similarity || 0);
        const text = line.text || '';
        return '<div class="hl-result-item ' + cls + '"><div class="hl-result-item-top">' +
          '<span class="hl-line-num">' + line.line_number + '</span>' + buildTypeBadge(line.match_type) +
          '<span class="hl-sim-pill ' + cls + '">' + sim + '%</span></div>' +
          '<div class="hl-result-item-text">' + escapeHtml(text.slice(0, 220)) + (text.length > 220 ? '…' : '') + '</div>' +
          '<div class="hl-result-item-src">' + (line.source_type === 'web' && line.source_url
            ? '<a href="' + escAttr(line.source_url) + '" target="_blank" rel="noopener" class="hl-source-link">' +
              escapeHtml(line.source_title || 'Web') + ' ↗</a>'
            : escapeHtml(line.source_title || 'Burim')) + '</div></div>';
      }).join('') + '</div>' +
      (flagged.length > 5
        ? '<div class="hl-result-more">+ ' + (flagged.length - 5) + ' rreshta të tjerë — hap temën për pamje të plotë</div>'
        : '');
  }

  function renderAnalysisSources(d) {
    const container = document.getElementById('res-matches');
    const aiBanner = document.getElementById('res-ai-banner');
    if (!container) return;

    const aiScore = Math.round(Number(d.ai_likelihood_score || 0));
    if (aiBanner) {
      if (aiScore >= 20) {
        const aiColor = aiScore >= 55 ? 'var(--red)' : aiScore >= 35 ? 'var(--orange)' : 'var(--gold)';
        aiBanner.style.display = 'block';
        aiBanner.className = 'ai-likelihood-banner';
        aiBanner.innerHTML =
          '<div class="ai-likelihood-head"><span>Gjasë e tekstit të gjeneruar nga AI</span>' +
          '<strong style="color:' + aiColor + '">' + aiScore + '%</strong></div>' +
          '<div class="ai-likelihood-bar"><div class="ai-likelihood-fill" style="width:' + aiScore + '%;background:' + aiColor + '"></div></div>' +
          '<p class="ai-likelihood-note">Vlerësim heuristik lokal — kontrolloni manualisht stilin dhe burimet.</p>';
      } else {
        aiBanner.style.display = 'none';
        aiBanner.innerHTML = '';
      }
    }

    const internal = (d.internal_sources?.length ? d.internal_sources : (d.matches || []))
      .filter(m => m.source_type === 'internal');
    const web = (d.web_sources?.length ? d.web_sources : (d.matches || []))
      .filter(m => m.source_type === 'web');
    const lineHighlights = d.line_highlights || [];

    if (!internal.length && !web.length && !lineHighlights.length) {
      container.innerHTML = '<div class="empty"><p>Nuk u gjetën burime të dyshimta.</p></div>';
      return;
    }

    let html = '';
    if (lineHighlights.length) {
      const bySource = {};
      lineHighlights.forEach(h => {
        const key = h.source_type === 'web' ? (h.source_url || 'web') : (h.thesis_id || h.source_title || 'internal');
        if (!bySource[key]) {
          bySource[key] = {
            title: h.source_title || 'Burim',
            url: h.source_url,
            thesisId: h.thesis_id,
            type: h.source_type,
            lines: [],
          };
        }
        bySource[key].lines.push(h);
      });
      html += '<div class="sources-section"><div class="sources-section-title">Burimet sipas temës / web</div>';
      Object.values(bySource).forEach(group => {
        html += '<div class="source-card ' + (group.type === 'web' ? 'source-web' : 'source-internal') + '">' +
          '<div class="source-card-head"><div class="source-card-title">' +
          (group.type === 'web' && group.url
            ? '<a href="' + escAttr(group.url) + '" target="_blank" rel="noopener">' + escapeHtml(group.title) + ' ↗</a>'
            : escapeHtml(group.title)) +
          '</div><span class="source-card-meta">' + group.lines.length + ' rresht' + (group.lines.length > 1 ? 'a' : '') + '</span></div>';
        if (group.type === 'internal' && group.thesisId) {
          html += '<button type="button" class="btn-ghost btn-xs" style="margin-bottom:10px" onclick="openAdminThesis(' + group.thesisId + ')">Hap temën burimore</button>';
        }
        html += '<div class="hl-result-list" style="padding:0">';
        group.lines.slice(0, 8).forEach(h => { html += buildCompactHighlightItem(h); });
        html += '</div></div>';
      });
      html += '</div>';
    }

    if (internal.length && !lineHighlights.length) {
      html += '<div class="sources-section"><div class="sources-section-title">Plagjiaturë interne — temat burimore</div>';
      internal.slice(0, 12).forEach(m => {
        const pct = Math.round(Number(m.similarity_score ?? m.similarity ?? 0));
        const tid = parseThesisIdFromMatch(m);
        html += '<div class="source-card source-internal"><div class="source-card-head"><div>' +
          '<div class="source-card-title">' + escapeHtml(m.source_title || 'Tema interne') + '</div></div>' +
          '<div class="source-card-actions"><span class="source-pct" style="color:' + scoreColor(pct) + '">' + pct + '%</span>' +
          (tid ? '<button type="button" class="btn-ghost btn-xs" onclick="openAdminThesis(' + tid + ')">Hap</button>' : '') +
          '</div></div></div>';
      });
      html += '</div>';
    }

    if (web.length) {
      html += '<div class="sources-section"><div class="sources-section-title">Burime web — faqet burimore</div>';
      web.forEach((m, idx) => {
        const pct = Math.round(Number(m.similarity_score ?? m.similarity ?? 0));
        const url = m.source_url || m.source || '';
        const title = m.source_title || url || 'Burim web';
        const thesisBit = m.thesis_phrase || m.thesis_excerpt || '';
        const webBit = m.web_snippet_full || m.matched_text || m.original_text || '';
        const panelId = 'web-src-' + idx;
        const verified = m.verified || m.exact_web_match;
        html += '<div class="source-card source-web"><div class="source-card-head"><div>' +
          '<div class="source-card-title">' + (url
            ? '<a href="' + escAttr(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(title) + ' ↗</a>'
            : escapeHtml(title)) + '</div></div>' +
          '<div class="source-card-actions">' +
          (verified ? '<span class="web-verified-badge">✓ Teksti u gjet</span>' : '') +
          '<span class="source-pct" style="color:' + scoreColor(pct) + '">' + pct + '%</span></div></div>' +
          (url ? '<a class="source-web-link" href="' + escAttr(url) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(url) + '</a>' : '') +
          '<div class="web-source-actions"><button type="button" class="btn-ghost btn-xs web-toggle-btn" onclick="toggleWebSource(\'' + panelId + '\')">Shfaq / fshih tekstin</button></div>' +
          '<div id="' + panelId + '" class="web-source-panel' + (verified ? ' open' : '') + '">' +
          (thesisBit ? '<div class="source-fragment-block hl-exact"><span>Në temën e studentit:</span>' + escapeHtml(thesisBit) + '</div>' : '') +
          (webBit ? '<div class="source-fragment-block source-from"><span>Në faqen web:</span>' + escapeHtml(webBit) + '</div>' : '') +
          '</div></div>';
      });
      html += '</div>';
    }

    container.innerHTML = html;
  }

  function displayAdminResult(d, meta) {
    _lastAdminResult = d;
    _currentReportThesisId = meta.thesisId || d.thesis_id || _currentReportThesisId || null;

    const title = meta.title || d.thesis_title || d.title || '';
    const name = meta.name || '';
    document.getElementById('modal-sub-text').textContent =
      (title ? title + ' · ' : '') + (name || 'Admin');

    const cov = d.coverage || d.match_statistics?.coverage || {};
    const score = Math.round(d.overall_score != null ? d.overall_score : (cov.overall_pct != null ? cov.overall_pct : (meta.pct != null ? meta.pct : 0)));
    const internal = Math.round(d.internal_score != null ? d.internal_score : (cov.internal_pct || 0));
    const web = Math.round(d.web_score != null ? d.web_score : (cov.web_pct || 0));
    const aiScore = Math.round(Number(d.ai_likelihood_score || 0));
    const aiDetected = !!(d.ai_detected || aiScore >= 35);
    const autoApproved = !!(d.auto_approved || d.workflow_status === 'approved');
    const riskLevel = (d.risk_level || (score >= 70 ? 'critical' : score >= 50 ? 'high' : score >= 25 ? 'medium' : 'low')).toString().toLowerCase();
    const riskMap = {
      critical: { label: 'Rrezik kritik', badge: 'badge b-red', ring: 'danger', color: 'var(--red)', verdict: 'Plagjiaturë kritike (mbulim unik) — kërkon kontroll të menjëhershëm.' },
      high: { label: 'Plagjiaturë e lartë', badge: 'badge b-orange', ring: 'danger', color: 'var(--orange)', verdict: 'Mbulim unik i lartë — shqyrto burimet dhe rreshtat e theksuar.' },
      medium: { label: 'Plagjiaturë mesatare', badge: 'badge b-yellow', ring: 'warning', color: 'var(--gold)', verdict: 'Ka përputhje të dyshimta — verifiko burimet para vendimit.' },
      low: { label: 'Rrezik i ulët', badge: 'badge b-green', ring: 'ok', color: 'var(--green)', verdict: 'Plagjiatura totale (mbulim unik) është e ulët — tema duket e pranueshme.' },
    };
    const riskInfo = autoApproved
      ? { label: 'Ka kaluar', badge: 'badge b-green', ring: 'ok', color: 'var(--green)', verdict: 'Tema kaloi automatikisht: plagjiatura totale ≤ pragut (AI nuk llogaritet në total).' }
      : (riskMap[riskLevel] || riskMap.low);

    const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    setText('res-pct', score + '%');
    setText('res-int', internal + '%');
    setText('res-web', web + '%');
    setText('res-ai', aiDetected ? ('Po · ' + aiScore + '%') : ('Jo · ' + aiScore + '%'));
    setText('res-ai-note', aiDetected
      ? 'I zbuluar — nuk shtohet në plagjiaturën totale / pragun e aprovimit.'
      : 'Nuk u zbulua sinjal i fortë AI; gjithsesi nuk hyn në total.');
    setText('res-summary', d.summary || 'Analiza u përfundua. Shiko rreshtat e theksuar dhe burimet më poshtë.');
    setText('res-verdict', riskInfo.verdict || '');
    setText('res-thesis-label', title ? ('Tema: ' + title) : (_currentReportThesisId ? ('Tema #' + _currentReportThesisId) : 'Rezultati i detajuar i plagjiaturës'));

    const intBar = document.getElementById('res-int-bar');
    const webBar = document.getElementById('res-web-bar');
    const aiBar = document.getElementById('res-ai-bar');
    if (intBar) intBar.style.width = Math.min(100, internal) + '%';
    if (webBar) webBar.style.width = Math.min(100, web) + '%';
    if (aiBar) aiBar.style.width = Math.min(100, aiScore) + '%';

    const ring = document.getElementById('score-ring');
    const badge = document.getElementById('res-risk');
    const hero = document.getElementById('report-hero');
    if (ring) ring.className = 'score-ring ' + riskInfo.ring;
    if (hero) hero.className = 'report-hero ' + riskInfo.ring;
    if (badge) {
      badge.textContent = riskInfo.label;
      badge.className = riskInfo.badge;
      badge.style.color = riskInfo.color;
    }

    const banner = document.getElementById('res-auto-banner');
    if (banner) {
      if (autoApproved) {
        banner.style.display = 'block';
        banner.className = 'auto-pass-banner';
        banner.innerHTML = '<strong>✓ Tema ka kaluar automatikisht</strong>Ngjashmëria ' + score + '% — nën ' + AUTO_PASS_THRESHOLD + '%.';
      } else {
        banner.style.display = 'none';
        banner.innerHTML = '';
      }
    }

    renderMatchStatistics(d);
    renderSourceDocuments(d);
    renderResultHighlights(d);
    renderAnalysisSources(d);

    const hlBtn = document.getElementById('res-open-highlighted');
    if (hlBtn) {
      const hasHl = (d.line_highlights || []).some(l => l.match_type)
        || (d.highlighted_document || []).some(l => l.match_type);
      hlBtn.style.display = (_currentReportThesisId && hasHl) ? 'block' : (_currentReportThesisId ? 'block' : 'none');
    }
  }

  window.openAdminThesisFromReport = function () {
    if (!_currentReportThesisId) {
      alert('Nuk ka temë të lidhur me këtë raport');
      return;
    }
    closeResultBtn();
    window.openAdminThesis(_currentReportThesisId);
  };

  window.showResult = async function (opts) {
    const resultId = typeof opts === 'object' ? opts.resultId : arguments[3];
    const name = typeof opts === 'object' ? opts.name : arguments[0];
    const title = typeof opts === 'object' ? (opts.title || '') : '';
    const pct = typeof opts === 'object' ? opts.pct : arguments[1];
    const thesisId = typeof opts === 'object' ? (opts.thesisId || null) : null;

    _currentReportResultId = resultId || null;
    _currentReportThesisId = thesisId || null;
    const overlay = document.getElementById('result-overlay');
    const pdfBtn = document.getElementById('btn-report-pdf');
    if (pdfBtn) pdfBtn.style.display = 'none';
    document.getElementById('modal-sub-text').textContent = (title ? title + ' · ' : '') + (name || '—');

    // Placeholder while loading
    displayAdminResult({
      overall_score: pct,
      summary: resultId ? 'Duke ngarkuar raportin e plotë…' : 'Nuk ka rezultat të lidhur.',
      matches: [],
      line_highlights: [],
    }, { name, title, pct, thesisId });

    if (resultId) {
      try {
        const result = await apiFetch('/analysis/result/' + resultId);
        if (result.thesis_id) _currentReportThesisId = result.thesis_id;
        displayAdminResult(result, {
          name,
          title: title || result.thesis_title || result.title || '',
          pct: result.overall_score != null ? Math.round(result.overall_score) : pct,
          thesisId: _currentReportThesisId,
        });
        if (pdfBtn && result.status === 'completed') pdfBtn.style.display = '';
      } catch (e) {
        if (pdfBtn) pdfBtn.style.display = 'none';
        setTextSafe('res-summary', e.message || 'Nuk u ngarkua raporti i plotë.');
      }
    }

    overlay.classList.add('open');
    const body = document.getElementById('admin-report-body');
    if (body) body.scrollTop = 0;
  };

  function setTextSafe(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  window.downloadReportPdf = async function () {
    if (!_currentReportResultId) return;
    const pdfBtn = document.getElementById('btn-report-pdf');
    if (pdfBtn) { pdfBtn.disabled = true; pdfBtn.textContent = 'Duke gjeneruar...'; }
    try {
      const r = await fetch(API + '/reports/' + _currentReportResultId + '/pdf', {
        headers: { Authorization: 'Bearer ' + TOKEN },
      });
      if (!r.ok) {
        let detail = 'PDF nuk u gjenerua';
        try { detail = apiError((await r.json()).detail) || detail; } catch (_) {}
        throw new Error(detail);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'raport_plagjiature_' + _currentReportResultId + '.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert(e.message || 'Gabim gjatë shkarkimit të PDF');
    } finally {
      if (pdfBtn) { pdfBtn.disabled = false; pdfBtn.textContent = 'Shkarko PDF'; }
    }
  };

  window.closeResult = function (e) { if (e.target === document.getElementById('result-overlay')) closeResultBtn(); };
  window.closeResultBtn = function () {
    document.getElementById('result-overlay').classList.remove('open');
    _currentReportResultId = null;
    _currentReportThesisId = null;
    _lastAdminResult = null;
    const pdfBtn = document.getElementById('btn-report-pdf');
    if (pdfBtn) pdfBtn.style.display = 'none';
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeResultBtn(); });

  async function callAgent(userMsg) {
    try {
      const data = await apiFetch('/admin/agent/chat', { method: 'POST', body: { message: userMsg } });
      return data.reply || '';
    } catch (e) {
      return fallbackAgentReply(userMsg);
    }
  }

  function fallbackAgentReply(msg) {
    const lower = msg.toLowerCase();
    const s = dashboardData?.stats || {};
    const items = dashboardData?.recent_theses || [];
    if (lower.includes('pritje') || lower.includes('student')) {
      return 'Nuk u lidh me serverin. Rifresko faqen dhe provo përsëri — agenti lexon të dhënat live nga databaza.';
    }
    if (lower.includes('statistik') || lower.includes('departament')) {
      const lines = (dashboardData?.departments || []).map(d => '• ' + d.name + ': ' + d.count).join('\n');
      return 'Statistikat (offline):\n\n• Temat: ' + (s.total_theses || 0) + '\n• Analizuar: ' + (s.analyzed_theses || 0) + (lines ? '\n\n' + lines : '');
    }
    return 'Nuk u lidh me agentin. Kontrollo që serveri FastAPI po punon dhe je i kyçur si admin.';
  }

  window.sendMessage = async function () {
    const input = document.getElementById('chat-input');
    const msgs = document.getElementById('chat-messages');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    const now = new Date().toLocaleTimeString('sq', { hour: '2-digit', minute: '2-digit' });
    msgs.innerHTML += '<div class="msg user fade-in"><div class="msg-av">' + initials('U') + '</div><div><div class="msg-bubble">' + escapeHtml(text) + '</div><div class="msg-time">' + now + '</div></div></div>';
    msgs.scrollTop = msgs.scrollHeight;
    const tid = 'typing-' + Date.now();
    msgs.innerHTML += '<div class="msg agent" id="' + tid + '"><div class="msg-av">AI</div><div><div class="msg-bubble"><div class="typing-dots"><span></span><span></span><span></span></div></div></div></div>';
    msgs.scrollTop = msgs.scrollHeight;
    const response = await callAgent(text);
    const el = document.getElementById(tid);
    if (el) {
      const t2 = new Date().toLocaleTimeString('sq', { hour: '2-digit', minute: '2-digit' });
      el.innerHTML = '<div class="msg-av">AI</div><div><div class="msg-bubble">' + escapeHtml(response).replace(/\n/g, '<br>') + '</div><div class="msg-time">' + t2 + '</div></div>';
    }
    msgs.scrollTop = msgs.scrollHeight;
  };

  window.sendSuggestion = function (btn) {
    document.getElementById('chat-input').value = btn.textContent;
    sendMessage();
  };

  window.saveSettings = async function () {
    try {
      const data = await apiFetch('/admin/settings', {
        method: 'POST',
        body: {
          settings: {
            plagiarism_threshold: document.getElementById('set-threshold')?.value || '30',
            api_endpoint: document.getElementById('set-api')?.value || API,
            report_lang: document.getElementById('set-lang')?.value || 'Shqip',
          },
        },
      });
      adminSettings = { ...adminSettings, ...(data.settings || {}) };
      adminSettings.plagiarism_threshold = document.getElementById('set-threshold')?.value || adminSettings.plagiarism_threshold;
      applySettingsLabels();
      if (currentPage === 'dashboard') loadDashboard();
      else if (currentPage === 'raporte') loadRaporte();
      else if (currentPage === 'tezat') { loadTezat(); renderTezat(); }
      alert('✓ Cilësimet u ruajtën.');
    } catch (e) { alert('Gabim: ' + e.message); }
  };

  window.testConnection = async function () {
    try {
      const res = await fetch(apiOrigin() + '/health', { signal: AbortSignal.timeout(5000) });
      const data = await res.json().catch(() => ({}));
      alert(res.ok ? '✓ Serveri aktiv.\nDatabaza: ' + (data.legacy_database?.ok ? 'OK' : '—') : '⚠ Gabim serveri.');
    } catch { alert('✗ Nuk u lidh me ' + apiOrigin()); }
  };

  function checkAuth() {
    TOKEN = localStorage.getItem('admin_token') || localStorage.getItem('token');
    if (!TOKEN) { window.location.href = 'ballina.html'; return; }
    const raw = localStorage.getItem('admin_user') || localStorage.getItem('user');
    if (raw) {
      try {
        const u = JSON.parse(raw);
        if (u.role && u.role !== 'admin') { window.location.href = 'ballina.html'; return; }
        const nm = document.querySelector('.user-name');
        const rl = document.querySelector('.user-role');
        const av = document.querySelector('.user-avatar');
        if (nm) nm.textContent = u.full_name || u.username || 'Admin';
        if (rl) rl.textContent = 'Administrator';
        if (av) av.textContent = initials(u.full_name || u.username);
      } catch (e) { /* ignore */ }
    }
  }

  document.addEventListener('DOMContentLoaded', async () => {
    checkAuth();
    await loadAdminSettings();
    loadDepartmentSelects();
    loadDashboard();
  });
})();
