// Small DOM toolkit for the console. Data from the API is always inserted as
// text (textContent), never as HTML; only the constant icon markup below is.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'html') node.innerHTML = value; // constant markup only (icons)
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

const ICON_PATHS = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="2"/><rect x="14" y="3" width="7" height="5" rx="2"/><rect x="14" y="12" width="7" height="9" rx="2"/><rect x="3" y="16" width="7" height="5" rx="2"/>',
  usage: '<path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19H2"/>',
  chat: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12Z"/>',
  star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9Z"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
  book: '<path d="M4 4h6a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4Z"/><path d="M20 4h-6a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h7Z"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><circle cx="17" cy="9" r="2.5"/><path d="M16 14.2a5 5 0 0 1 6 4.8"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  refresh: '<path d="M21 12a9 9 0 0 1-15.4 6.4L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8"/><path d="M21 3v5h-5"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="M10 17l-5-5 5-5"/><path d="M5 12h11"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sort: '<path d="m8 9 4-4 4 4"/><path d="M12 5v14"/>',
  trend_up: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  trend_down: '<path d="m3 7 6 6 4-4 8 8"/><path d="M15 17h6v-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  dollar: '<path d="M12 2v20"/><path d="M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>',
  bulb: '<path d="M9 18h6M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7V16h8v-1.3A7 7 0 0 0 12 2Z"/>',
  sessions: '<path d="M7 8h10M7 12h6"/><rect x="3" y="4" width="18" height="16" rx="3"/>',
  table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="m6.3 6.3 2.1 2.1M15.6 15.6l2.1 2.1M6.3 17.7l2.1-2.1M15.6 8.4l2.1-2.1"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  upload: '<path d="M12 21V9M7 14l5-5 5 5"/><path d="M5 3h14"/>',
};

export function icon(name, extraClass = '') {
  const span = h('span', { class: `icon-wrap ${extraClass}`, 'aria-hidden': 'true' });
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICON_PATHS[name] || ''}</svg>`;
  return span.firstChild;
}

export function starRating(value) {
  const wrap = h('span', { class: 'stars', 'aria-label': `${value} out of 5` });
  for (let index = 1; index <= 5; index += 1) {
    const star = icon('star');
    star.setAttribute('fill', 'currentColor');
    if (index > value) star.classList.add('off');
    wrap.append(star);
  }
  return wrap;
}

// ---------- Formatting ----------
const compactFormat = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
const integerFormat = new Intl.NumberFormat('en');

export const fmt = {
  int: (value) => integerFormat.format(Math.round(value || 0)),
  compact: (value) => (Math.abs(value) >= 10000 ? compactFormat.format(value) : integerFormat.format(Math.round(value || 0))),
  percent: (value, digits = 0) => `${(value * 100).toFixed(digits)}%`,
  money: (value, currency) => {
    const digits = value !== 0 && Math.abs(value) < 1 ? 4 : 2;
    return `${currency}${value.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: digits })}`;
  },
  ms: (value) => (value === null || value === undefined ? '-' : value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`),
  date: (value) => new Date(value).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Singapore' }),
  dateTime: (value) => new Date(value).toLocaleString('en-SG', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Singapore' }),
  shortDay: (isoDate) => new Date(`${isoDate}T00:00:00+08:00`).toLocaleDateString('en-SG', { day: 'numeric', month: 'short', timeZone: 'Asia/Singapore' }),
  hour: (hour) => `${hour % 12 || 12}${hour < 12 ? 'am' : 'pm'}`,
  relative: (value) => {
    const seconds = (Date.now() - new Date(value).getTime()) / 1000;
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  },
};

export function countUp(node, target, format = fmt.compact, duration = 700) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || !Number.isFinite(target)) {
    node.textContent = format(target);
    return;
  }
  const start = performance.now();
  const step = (now) => {
    const progress = Math.min(1, (now - start) / duration);
    const eased = 1 - (1 - progress) ** 3;
    node.textContent = format(target * eased);
    if (progress < 1) requestAnimationFrame(step);
    else node.textContent = format(target);
  };
  requestAnimationFrame(step);
}

// ---------- Feedback ----------
export function toast(message, tone = '') {
  const node = h('div', { class: `toast ${tone}` }, message);
  document.getElementById('toasts').append(node);
  setTimeout(() => {
    node.classList.add('leaving');
    setTimeout(() => node.remove(), 320);
  }, 3200);
}

function trapEscape(close) {
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  return () => document.removeEventListener('keydown', onKey);
}

export function openDrawer(title, body) {
  const previous = document.activeElement;
  const overlay = h('div', { class: 'overlay' });
  const closeButton = h('button', { class: 'button icon ghost', 'aria-label': 'Close' }, icon('close'));
  const drawer = h('aside', { class: 'drawer', role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'drawer-head' }, h('h2', {}, title), closeButton),
    h('div', { class: 'drawer-body' }, body));
  let release;
  const close = () => { release(); overlay.remove(); drawer.remove(); previous?.focus?.(); };
  release = trapEscape(close);
  overlay.addEventListener('click', close);
  closeButton.addEventListener('click', close);
  document.body.append(overlay, drawer);
  closeButton.focus();
  return close;
}

export function openModal({ title, text, body, confirmLabel = 'Save', danger = false, wide = false, onConfirm }) {
  const previous = document.activeElement;
  const overlay = h('div', { class: 'overlay' });
  const error = h('div', { class: 'error-text', role: 'alert' });
  const confirm = h('button', { class: `button ${danger ? 'danger' : 'primary'}`, type: 'submit' }, confirmLabel);
  const cancel = h('button', { class: 'button', type: 'button' }, 'Cancel');
  const form = h('form', {}, body || '', error, h('div', { class: 'actions' }, cancel, confirm));
  const modal = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('h2', {}, title), text ? h('p', {}, text) : '', form);
  let release;
  const close = () => { release(); overlay.remove(); modal.remove(); previous?.focus?.(); };
  release = trapEscape(close);
  overlay.addEventListener('click', close);
  cancel.addEventListener('click', close);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    error.textContent = '';
    confirm.disabled = true;
    try {
      await onConfirm(new FormData(form));
      close();
    } catch (exc) {
      error.textContent = exc.message || 'Something went wrong.';
      confirm.disabled = false;
    }
  });
  document.body.append(overlay, modal);
  (form.querySelector('input, select, textarea') || confirm).focus();
}

// ---------- Controls ----------
export function segmented(options, value, onChange, label) {
  const thumb = h('span', { class: 'thumb', 'aria-hidden': 'true' });
  const group = h('div', { class: 'segmented', role: 'group', 'aria-label': label }, thumb);
  const buttons = options.map((option) => {
    const button = h('button', { type: 'button', 'aria-pressed': String(option.value === value) }, option.label);
    button.addEventListener('click', () => {
      buttons.forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
      place(button);
      onChange(option.value);
    });
    group.append(button);
    return button;
  });
  const place = (button) => {
    thumb.style.width = `${button.offsetWidth}px`;
    thumb.style.transform = `translateX(${button.offsetLeft - 2}px)`;
  };
  requestAnimationFrame(() => place(buttons.find((button) => button.getAttribute('aria-pressed') === 'true') || buttons[0]));
  return group;
}

export function searchBox(placeholder, value, onInput, { shortcut = false } = {}) {
  const input = h('input', { class: 'input', type: 'search', placeholder, value: value || '', 'aria-label': placeholder });
  let timer;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => onInput(input.value.trim()), 160);
  });
  return h('div', { class: 'search-box' }, icon('search'), input, shortcut ? h('kbd', {}, '/') : '');
}

export function select(options, value, onChange, label) {
  const node = h('select', { class: 'select', 'aria-label': label });
  for (const option of options) {
    node.append(h('option', { value: option.value, selected: option.value === value }, option.label));
  }
  node.addEventListener('change', () => onChange(node.value));
  return node;
}

export function empty(title, text) {
  const wrap = h('div', { class: 'empty' });
  wrap.innerHTML = '<svg viewBox="0 0 120 120" fill="none" aria-hidden="true"><circle cx="60" cy="60" r="52" fill="currentColor" opacity="0.08"/><rect x="34" y="40" width="52" height="40" rx="10" stroke="currentColor" stroke-width="3" opacity="0.5"/><path d="M44 56h32M44 66h20" stroke="currentColor" stroke-width="3" stroke-linecap="round" opacity="0.5"/><circle cx="86" cy="38" r="7" fill="#ff6a2b"/></svg>';
  wrap.append(h('strong', {}, title), text ? h('span', {}, text) : '');
  return wrap;
}

// ---------- Data table: search, sort and pages ----------
export function dataTable({ columns, rows, search = '', searchKeys = [], sort, pageSize = 25, onRowClick, emptyTitle = 'Nothing here yet', emptyText = '' }) {
  const state = { search: search.toLowerCase(), sortKey: sort?.key, sortDir: sort?.dir || 'desc', page: 0 };
  const wrap = h('div', {});

  const visibleRows = () => {
    let list = rows;
    if (state.search) {
      list = list.filter((row) => searchKeys.some((key) => String(row[key] ?? '').toLowerCase().includes(state.search)));
    }
    if (state.sortKey) {
      const column = columns.find((item) => item.key === state.sortKey);
      const valueOf = column?.sortValue || ((row) => row[state.sortKey]);
      list = [...list].sort((a, b) => {
        const left = valueOf(a); const right = valueOf(b);
        const order = left === right ? 0 : left === null || left === undefined ? 1 : right === null || right === undefined ? -1 : left > right ? 1 : -1;
        return state.sortDir === 'asc' ? order : -order;
      });
    }
    return list;
  };

  const render = () => {
    const list = visibleRows();
    const pages = Math.max(1, Math.ceil(list.length / pageSize));
    state.page = Math.min(state.page, pages - 1);
    const pageRows = list.slice(state.page * pageSize, (state.page + 1) * pageSize);
    wrap.replaceChildren();
    if (!list.length) {
      wrap.append(empty(state.search ? 'No matches' : emptyTitle, state.search ? 'Try a different search.' : emptyText));
      return;
    }
    const headRow = h('tr', {});
    for (const column of columns) {
      const th = h('th', { class: column.align === 'right' ? 'num' : '', scope: 'col' });
      if (column.sortable === false) {
        th.textContent = column.label;
      } else {
        if (state.sortKey === column.key) th.setAttribute('aria-sort', state.sortDir === 'asc' ? 'ascending' : 'descending');
        th.append(h('button', {
          type: 'button',
          onclick: () => {
            state.sortDir = state.sortKey === column.key && state.sortDir === 'desc' ? 'asc' : 'desc';
            state.sortKey = column.key;
            render();
          },
        }, column.label, icon('sort')));
      }
      headRow.append(th);
    }
    const body = h('tbody', {});
    for (const row of pageRows) {
      const tr = h('tr', { class: onRowClick ? 'clickable' : '' });
      if (onRowClick) {
        tr.tabIndex = 0;
        tr.addEventListener('click', () => onRowClick(row));
        tr.addEventListener('keydown', (event) => { if (event.key === 'Enter') onRowClick(row); });
      }
      for (const column of columns) {
        const content = column.render ? column.render(row) : row[column.key];
        tr.append(h('td', { class: `${column.align === 'right' ? 'num' : ''} ${column.clip ? 'clip' : ''}` }, content ?? '-'));
      }
      body.append(tr);
    }
    const prev = h('button', { class: 'button', disabled: state.page === 0, onclick: () => { state.page -= 1; render(); } }, 'Previous');
    const next = h('button', { class: 'button', disabled: state.page >= pages - 1, onclick: () => { state.page += 1; render(); } }, 'Next');
    wrap.append(
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', {}, headRow), body)),
      h('div', { class: 'table-foot' },
        h('span', {}, `${fmt.int(list.length)} ${list.length === 1 ? 'item' : 'items'}${pages > 1 ? ` · page ${state.page + 1} of ${pages}` : ''}`),
        pages > 1 ? h('div', { class: 'pager' }, prev, next) : ''),
    );
  };

  render();
  wrap.setSearch = (value) => { state.search = (value || '').toLowerCase(); state.page = 0; render(); };
  return wrap;
}

export function toCsv(columns, rows) {
  // Visitor text can start with = + - @; prefix it so spreadsheets do not run it as a formula.
  const escape = (value) => {
    let text = String(value ?? '');
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  };
  return [columns.map((column) => escape(column.label)).join(','), ...rows.map((row) => columns.map((column) => escape(column.csv ? column.csv(row) : row[column.key])).join(','))].join('\n');
}

export function download(filename, text) {
  const link = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: filename });
  document.body.append(link);
  link.click();
  setTimeout(() => { URL.revokeObjectURL(link.href); link.remove(); }, 0);
}

// ---------- Markdown (model answers) ----------
// A small renderer for the Markdown SNAIC Bot writes: headings, lists, tables,
// bold, italics, code and links. It builds DOM nodes, so answer text can never
// become HTML, and only http(s) and mailto links are kept.
export function markdown(source) {
  const root = h('div', { class: 'md' });
  const lines = String(source || '').replace(/\r\n/g, '\n').split('\n');
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      root.append(h(`h${Math.min(6, heading[1].length + 2)}`, {}, inline(heading[2])));
      index += 1;
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const rows = [];
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) rows.push(lines[index++]);
      const cells = (row) => row.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
      const body = rows.filter((row) => !/^\s*\|?\s*:?-{2,}/.test(row));
      const [head, ...rest] = body;
      root.append(h('div', { class: 'md-table' }, h('table', { class: 'table' },
        h('thead', {}, h('tr', {}, cells(head).map((cell) => h('th', {}, inline(cell))))),
        h('tbody', {}, rest.map((row) => h('tr', {}, cells(row).map((cell) => h('td', {}, inline(cell)))))))));
      continue;
    }
    const listMatch = line.match(/^\s*([-*•]|\d+[.)])\s+/);
    if (listMatch) {
      const ordered = /\d/.test(listMatch[1]);
      const list = h(ordered ? 'ol' : 'ul', {});
      while (index < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[index])) {
        list.append(h('li', {}, inline(lines[index].replace(/^\s*([-*•]|\d+[.)])\s+/, ''))));
        index += 1;
      }
      root.append(list);
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s|^\s*([-*•]|\d+[.)])\s+|^\s*\|/.test(lines[index])) {
      paragraph.push(lines[index++]);
    }
    root.append(h('p', {}, inline(paragraph.join(' '))));
  }
  return root;
}

function inline(text) {
  const nodes = [];
  const pattern = /\[([^\]]+)\]\(([^)\s]+)\)|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    if (match[1] !== undefined) {
      const safe = /^(https?:|mailto:)/i.test(match[2]);
      nodes.push(safe ? h('a', { href: match[2], target: '_blank', rel: 'noopener noreferrer' }, match[1]) : match[1]);
    } else if (match[3] !== undefined) nodes.push(h('strong', {}, match[3]));
    else if (match[4] !== undefined) nodes.push(h('em', {}, match[4]));
    else nodes.push(h('code', {}, match[5]));
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}
