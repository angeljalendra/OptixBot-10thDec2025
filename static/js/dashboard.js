/* global io */
const state = {
  initial: {},
  lastUpdate: null
};

function fmt(n, digits = 2) {
  if (n === null || n === undefined || isNaN(n)) return '-';
  return Number(n).toFixed(digits);
}

function renderOverview(data) {
  const pnl = (data.pnl || {}).total || 0;
  const winRate = (data.performance || {}).win_rate || 0;
  const openPositions = (data.positions || []).length || 0;
  const signals = (data.signals || []).length || 0;
  document.getElementById('metric-pnl').textContent = fmt(pnl, 0);
  document.getElementById('metric-winrate').textContent = fmt(winRate, 1) + '%';
  document.getElementById('metric-open-positions').textContent = String(openPositions);
  document.getElementById('metric-signals').textContent = String(signals);
}

function renderPositions(list) {
  const tbody = document.getElementById('positions-body');
  tbody.innerHTML = '';
  (list || []).forEach(p => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${p.symbol || '-'}</td>
      <td class="right">${fmt(p.qty || p.quantity || 0, 0)}</td>
      <td class="right">${fmt(p.avg_price || 0)}</td>
      <td class="right">${fmt(p.ltp || 0)}</td>
      <td class="right">${fmt(p.pnl || 0, 0)}</td>
      <td>${(p.side || p.position_type || '').toUpperCase()}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderSignals(list) {
  const tbody = document.getElementById('signals-body');
  tbody.innerHTML = '';
  (list || []).forEach(s => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${s.symbol || '-'}</td>
      <td>${(s.type || s.side || '').toUpperCase()}</td>
      <td class="right">${fmt(s.confidence || 0, 1)}</td>
      <td class="right">${fmt(s.rr_ratio || 0, 2)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function applyInitial() {
  renderOverview(state.initial || {});
  renderPositions((state.initial || {}).positions || []);
  renderSignals((state.initial || {}).signals || []);
}

function setupSocket() {
  if (typeof io !== 'undefined') {
    const socket = io();
    socket.on('connect', () => {
      socket.emit('request_update');
    });
    socket.on('portfolio_update', (data) => {
      try {
        state.initial = data || {};
        state.lastUpdate = new Date();
        applyInitial();
      } catch (e) {
        console.error(e);
      }
    });
    document.getElementById('refresh').addEventListener('click', () => {
      socket.emit('request_update');
    });
    document.getElementById('strategy').addEventListener('change', (e) => {
      socket.emit('strategy_change', { strategy: e.target.value });
    });
  } else {
    const poll = async () => {
      try {
        const r = await fetch('/api/simple_state', { cache: 'no-store' });
        const j = await r.json();
        state.initial = j || {};
        state.lastUpdate = new Date();
        applyInitial();
      } catch (e) {
        // ignore
      }
    };
    document.getElementById('refresh').addEventListener('click', poll);
    document.getElementById('strategy').addEventListener('change', async (e) => {
      try {
        await fetch('/api/set_active_strategy', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ strategy: e.target.value })
        });
      } catch (e2) {}
      poll();
    });
    poll();
    setInterval(poll, 5000);
  }
}

function init() {
  try {
    const el = document.getElementById('initial-data');
    if (el) {
      state.initial = JSON.parse(el.textContent || '{}');
    }
  } catch (e) {
    state.initial = {};
  }
  applyInitial();
  setupSocket();
}

document.addEventListener('DOMContentLoaded', init);
