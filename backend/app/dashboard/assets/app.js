// SNAIC Console: admin dashboard for the SNAIC LLM backend.
import { api, session, settings as settingsStore, DEFAULT_SETTINGS, ApiError } from './api.js';
import { barChart, lineChart, chartPanel, destroyCharts, token, STATUS } from './charts.js';
import {
  h, icon, fmt, countUp, toast, openDrawer, openModal, segmented, searchBox, select, empty,
  dataTable, starRating, toCsv, download,
} from './ui.js';

const app = document.getElementById('app');
const state = {
  user: null,
  settings: settingsStore.load(),
  analytics: new Map(), // days -> response
  navOpen: false,
};

const PERIODS = [
  { value: 7, label: '7D' },
  { value: 30, label: '30D' },
  { value: 90, label: '90D' },
  { value: 365, label: '1Y' },
];

const ROUTES = {
  overview: { title: 'Overview', icon: 'overview', render: renderOverview, group: 'Insights' },
  usage: { title: 'Usage & cost', icon: 'usage', render: renderUsage, group: 'Insights' },
  conversations: { title: 'Conversations', icon: 'chat', render: renderConversations, group: 'Insights' },
  feedback: { title: 'Feedback', icon: 'star', render: renderFeedback, group: 'Insights' },
  models: { title: 'Models & health', icon: 'chip', render: renderModels, group: 'System' },
  knowledge: { title: 'Knowledge base', icon: 'book', render: renderKnowledge, group: 'System' },
  users: { title: 'Users', icon: 'users', render: renderUsers, group: 'System' },
  settings: { title: 'Settings', icon: 'settings', render: renderSettings, group: 'System' },
};

// ---------- Boot ----------
applyTheme();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (state.settings.theme === 'system' && state.user) route();
});
session.onExpired = () => { state.user = null; renderLogin('Your session has ended. Please sign in again.'); };
window.addEventListener('hashchange', () => state.user && route());
document.addEventListener('keydown', (event) => {
  if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
    const search = document.querySelector('.topbar input[type="search"]');
    if (search) { event.preventDefault(); search.focus(); }
  }
});

async function start() {
  if (!session.token) return renderLogin();
  try {
    await signedIn(await api.me());
  } catch {
    renderLogin();
  }
}

async function signedIn(user) {
  if (!user.is_admin) {
    session.token = '';
    renderLogin('This account cannot use the console. Ask an administrator for an admin account.');
    return;
  }
  state.user = user;
  if (!location.hash) location.hash = '#/overview';
  route();
}

function applyTheme() {
  const root = document.documentElement;
  if (state.settings.theme === 'system') delete root.dataset.theme;
  else root.dataset.theme = state.settings.theme;
}

function saveSettings(changes) {
  state.settings = { ...state.settings, ...changes };
  settingsStore.save(state.settings);
}

// ---------- Login ----------
function renderLogin(message = '') {
  destroyCharts();
  const error = h('div', { class: 'error-text', role: 'alert' }, message);
  const username = h('input', { class: 'input', name: 'username', autocomplete: 'username', required: true, id: 'login-user' });
  const password = h('input', { class: 'input', name: 'password', type: 'password', autocomplete: 'current-password', required: true, id: 'login-pass' });
  const submit = h('button', { class: 'button primary block', type: 'submit' }, 'Sign in');
  const card = h('div', { class: 'login-card' },
    brand(),
    h('h1', {}, 'Welcome back'),
    h('p', { class: 'lead' }, 'Sign in to manage SNAIC Bot and see how visitors use it.'),
    h('form', {
      onsubmit: async (event) => {
        event.preventDefault();
        error.textContent = '';
        submit.disabled = true;
        submit.replaceChildren(h('span', { class: 'spinner' }), 'Signing in');
        try {
          const result = await api.signIn(username.value.trim(), password.value);
          session.token = result.access_token;
          await signedIn(await api.me());
        } catch (exc) {
          error.textContent = exc.status === 401 ? 'Incorrect username or password.' : exc.message;
          card.classList.remove('shake');
          void card.offsetWidth;
          card.classList.add('shake');
          submit.disabled = false;
          submit.replaceChildren('Sign in');
        }
      },
    },
    h('div', { class: 'field' }, h('label', { for: 'login-user' }, 'Username'), username),
    h('div', { class: 'field' }, h('label', { for: 'login-pass' }, 'Password'), password),
    error,
    submit),
    h('p', { class: 'note' }, 'Accounts are created by an administrator. Ask for one if you need access.'));

  const art = h('div', { class: 'login-art' },
    h('div', {}, h('h2', {}, 'Every conversation, at a glance.'), h('p', {}, 'Usage, cost, feedback and model health for the SNAIC LLM backend, in one place.')));
  const illustration = h('div', {});
  illustration.innerHTML = LOGIN_ILLUSTRATION;
  art.append(illustration.firstChild);
  app.replaceChildren(h('main', { class: 'login' }, art, h('div', { class: 'login-panel' }, card)));
  username.focus();
}

function brand() {
  return h('div', { class: 'brand' },
    h('img', { src: 'assets/logo.svg', alt: '' }),
    h('div', {}, h('strong', {}, 'SNAIC Console'), h('span', {}, 'LLM backend')));
}

// ---------- Shell & routing ----------
function parseHash() {
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  return { name: ROUTES[path] ? path : 'overview', params: new URLSearchParams(query) };
}

function route() {
  const { name, params } = parseHash();
  destroyCharts();
  const content = h('div', { class: 'content view-enter' });
  app.replaceChildren(shell(name, content));
  document.title = `${ROUTES[name].title} · SNAIC Console`;
  ROUTES[name].render(content, params).catch((exc) => {
    if (exc instanceof ApiError && exc.status === 401) return;
    content.replaceChildren(empty('Something went wrong', exc.message || 'Please try again.'));
  });
}

function shell(active, content) {
  const nav = h('nav', { class: 'nav', 'aria-label': 'Main' });
  let group = '';
  for (const [name, item] of Object.entries(ROUTES)) {
    if (item.group !== group) {
      group = item.group;
      nav.append(h('div', { class: 'nav-label' }, group));
    }
    nav.append(h('a', {
      href: `#/${name}`,
      'aria-current': name === active ? 'page' : null,
      onclick: () => { root.classList.remove('nav-open'); },
    }, icon(item.icon), item.title));
  }
  const initials = state.user.username.slice(0, 2).toUpperCase();
  const sidebar = h('aside', { class: 'sidebar' },
    brand(),
    nav,
    h('div', { class: 'sidebar-foot' },
      h('div', { class: 'avatar', 'aria-hidden': 'true' }, initials),
      h('div', { class: 'who' }, h('strong', {}, state.user.username), h('span', {}, 'Administrator')),
      h('button', { class: 'button icon ghost', 'aria-label': 'Sign out', title: 'Sign out', onclick: signOut }, icon('logout'))));

  const topSearch = searchBox('Search conversations', '', () => {}, { shortcut: true });
  topSearch.querySelector('input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      const query = event.target.value.trim();
      location.hash = `#/conversations${query ? `?q=${encodeURIComponent(query)}` : ''}`;
    }
  });
  const topbar = h('header', { class: 'topbar' },
    h('button', { class: 'button icon ghost menu-button', 'aria-label': 'Menu', onclick: () => root.classList.toggle('nav-open') }, icon('menu')),
    h('div', { class: 'search' }, topSearch),
    h('div', { class: 'spacer' }),
    h('button', {
      class: 'button ghost',
      onclick: () => { state.analytics.clear(); route(); toast('Refreshed'); },
    }, icon('refresh'), 'Refresh'));

  const root = h('div', { class: 'shell' }, sidebar, h('div', { class: 'main' }, topbar, content));
  return root;
}

function signOut() {
  session.token = '';
  state.user = null;
  state.analytics.clear();
  location.hash = '';
  renderLogin();
  toast('Signed out');
}

function pageHead(title, subtitle, ...actions) {
  return h('div', { class: 'page-head' },
    h('div', {}, h('h1', {}, title), subtitle ? h('p', {}, subtitle) : ''),
    h('div', { class: 'toolbar' }, actions));
}

function card(title, subtitle, body, { actions = [], className = '' } = {}) {
  return h('section', { class: `card ${className}` },
    h('div', { class: 'card-head' },
      h('div', {}, h('h2', {}, title), subtitle ? h('p', {}, subtitle) : ''),
      h('div', { class: 'toolbar' }, actions)),
    body);
}

function kpi(label, iconName, value, { format = fmt.compact, delta, sub, invert = false } = {}) {
  const valueNode = h('div', { class: 'value' });
  if (typeof value === 'number') countUp(valueNode, value, format);
  else valueNode.textContent = value;
  let deltaNode = '';
  if (delta !== undefined && delta !== null && Number.isFinite(delta)) {
    const up = delta > 0;
    const good = invert ? !up : up;
    deltaNode = h('div', { class: `delta ${delta === 0 ? '' : good ? 'up' : 'down'}` },
      delta === 0 ? '' : icon(up ? 'trend_up' : 'trend_down'),
      `${delta > 0 ? '+' : ''}${fmt.percent(delta)} vs previous`);
  }
  return h('div', { class: 'card kpi' },
    h('div', { class: 'label' }, icon(iconName), label),
    valueNode,
    deltaNode,
    sub ? h('div', { class: 'sub' }, sub) : '');
}

function change(current, previous) {
  if (!previous) return current ? null : 0;
  return (current - previous) / previous;
}

async function getAnalytics(days) {
  if (!state.analytics.has(days)) state.analytics.set(days, await api.analytics(days));
  return state.analytics.get(days);
}

function periodControl(content, render) {
  return segmented(PERIODS, state.days || state.settings.defaultDays, async (days) => {
    state.days = days;
    content.classList.add('loading');
    await render(content).catch((exc) => toast(exc.message, 'bad'));
    content.classList.remove('loading');
  }, 'Period');
}

function costOf(totals, days) {
  const { inputPrice, outputPrice, monthlyFixedCost } = state.settings;
  const tokens = (totals.input_tokens / 1e6) * inputPrice + (totals.output_tokens / 1e6) * outputPrice;
  const fixed = monthlyFixedCost * (days / 30);
  return { tokens, fixed, total: tokens + fixed };
}

function answered(totals) {
  return Math.max(0, totals.completed - totals.fallback);
}

// ---------- Overview ----------
async function renderOverview(content) {
  const days = state.days || state.settings.defaultDays;
  const data = await getAnalytics(days);
  const { totals, previous_totals: previous } = data;
  const cost = costOf(totals, days);
  const answerRate = totals.chats ? answered(totals) / totals.chats : 0;
  const previousRate = previous.chats ? answered(previous) / previous.chats : 0;

  content.replaceChildren(
    pageHead('Overview', `SNAIC Bot activity over the last ${days === 365 ? 'year' : `${days} days`} (Singapore time)`, periodControl(content, renderOverview)),
    data.truncated ? h('div', { class: 'callout warn' }, 'This period has more conversations than one summary reads; the oldest are left out.') : '',
    h('div', { class: 'grid kpis stagger' },
      kpi('Conversations', 'chat', totals.chats, { delta: change(totals.chats, previous.chats) }),
      kpi('Visitor sessions', 'sessions', totals.sessions, { delta: change(totals.sessions, previous.sessions) }),
      kpi('Answered', 'check', answerRate, { format: (value) => fmt.percent(value), delta: totals.chats && previous.chats ? answerRate - previousRate : null, sub: `${fmt.int(totals.fallback)} found no matching content, ${fmt.int(totals.failed)} failed` }),
      kpi('Average rating', 'star', data.feedback.average_rating ?? '-', { format: (value) => value.toFixed(1), sub: `${fmt.int(data.feedback.count)} ratings` }),
      kpi('Response time', 'clock', data.latency.average_ms ?? '-', { format: fmt.ms, sub: data.latency.samples ? `p95 ${fmt.ms(data.latency.p95_ms)}` : 'Recorded from this release on' }),
      kpi('Estimated cost', 'dollar', cost.total, { format: (value) => fmt.money(value, state.settings.currency), sub: 'Set prices in Settings' }),
    ),
    h('div', { class: 'grid two stagger' },
      card('Conversations per day', 'Answered, no matching content, and failed', dailyChart(data)),
      card('Insights', 'What stands out this period', insightsList(data, days))),
    h('div', { class: 'grid thirds stagger' },
      card('Busiest hours', 'Conversations by hour of day', hourlyChart(data)),
      card('Top keywords', 'Words visitors use most. Select one to see those conversations.', keywordCloud(data.keywords)),
      card('Ratings', `${fmt.int(data.feedback.count)} feedback ratings`, ratingChart(data.feedback))),
    h('div', { class: 'grid halves stagger' },
      card('Most asked questions', 'Exact repeats, after removing punctuation', rankList(data.top_questions, 'No questions yet.')),
      card('When visitors chat', 'Day of week by hour', heatmap(data.weekday_hourly))),
  );
}

function dailyChart(data) {
  const labels = data.daily.map((day) => fmt.shortDay(day.date));
  const series = [
    { label: 'Answered', color: token('--series-1'), values: data.daily.map((day) => Math.max(0, day.completed - day.fallback)) },
    { label: 'No matching content', color: STATUS.warning, values: data.daily.map((day) => day.fallback) },
    { label: 'Failed', color: STATUS.critical, values: data.daily.map((day) => day.failed) },
  ];
  const panel = chartPanel({
    size: 'tall',
    series,
    table: { caption: 'Conversations per day', headers: ['Day', ...series.map((item) => item.label)], rows: data.daily.map((day, index) => [labels[index], ...series.map((item) => fmt.int(item.values[index]))]) },
    draw: (canvas) => barChart(canvas, { labels, stacked: true, datasets: series.map((item) => ({ label: item.label, data: item.values, backgroundColor: item.color })) }),
  });
  return h('div', {}, panel, h('div', { class: 'toolbar' }, panel.toggle));
}

function hourlyChart(data) {
  const labels = data.hourly.map((_, hour) => fmt.hour(hour));
  const panel = chartPanel({
    size: 'short',
    table: { caption: 'Conversations by hour', headers: ['Hour', 'Conversations'], rows: data.hourly.map((count, hour) => [labels[hour], fmt.int(count)]) },
    draw: (canvas) => barChart(canvas, { labels, datasets: [{ label: 'Conversations', data: data.hourly, backgroundColor: token('--series-1') }] }),
  });
  return h('div', {}, panel, h('div', { class: 'toolbar' }, panel.toggle));
}

function ratingChart(feedback) {
  const labels = ['1★', '2★', '3★', '4★', '5★'];
  const values = [1, 2, 3, 4, 5].map((score) => feedback.distribution[String(score)] || 0);
  if (!feedback.count) return empty('No ratings yet', 'Ratings appear when visitors close the chat and leave feedback.');
  const panel = chartPanel({
    size: 'short',
    table: { caption: 'Ratings', headers: ['Rating', 'Count'], rows: labels.map((label, index) => [label, fmt.int(values[index])]) },
    draw: (canvas) => barChart(canvas, { labels, datasets: [{ label: 'Ratings', data: values, backgroundColor: token('--series-1') }] }),
  });
  return h('div', {}, panel, h('div', { class: 'toolbar' }, panel.toggle));
}

function keywordCloud(keywords) {
  if (!keywords.length) return empty('No keywords yet');
  return h('div', { class: 'tag-cloud' }, keywords.map((item) => h('button', {
    class: 'tag',
    type: 'button',
    onclick: () => { location.hash = `#/conversations?q=${encodeURIComponent(item.label)}`; },
  }, item.label, h('b', {}, fmt.int(item.count)))));
}

function rankList(items, emptyText) {
  if (!items.length) return empty(emptyText);
  return h('ol', { class: 'rank-list' }, items.map((item, index) => h('li', { title: item.label },
    h('span', { class: 'rank' }, index + 1),
    h('span', { class: 'text' }, item.label),
    h('span', { class: 'count' }, fmt.int(item.count)))));
}

function heatmap(matrix) {
  const max = Math.max(1, ...matrix.flat());
  const grid = h('div', { class: 'heatmap', role: 'img', 'aria-label': 'Conversations by day of week and hour' });
  grid.append(h('span', {}));
  for (let hour = 0; hour < 24; hour += 1) grid.append(h('span', {}, hour % 6 === 0 ? fmt.hour(hour) : ''));
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const base = token('--series-1');
  matrix.forEach((row, dayIndex) => {
    grid.append(h('span', { class: 'day' }, days[dayIndex]));
    row.forEach((count, hour) => {
      const cell = h('span', { class: 'cell', title: `${days[dayIndex]} ${fmt.hour(hour)}: ${count} conversations`, tabindex: '0' });
      if (count) {
        cell.style.background = base;
        cell.style.opacity = String(0.15 + 0.85 * (count / max));
      }
      grid.append(cell);
    });
  });
  return grid;
}

function insightsList(data, days) {
  const items = [];
  const { totals, previous_totals: previous } = data;
  if (!totals.chats) {
    return empty('No conversations yet', 'Insights appear once visitors start chatting.');
  }
  const growth = change(totals.chats, previous.chats);
  if (growth !== null && previous.chats) {
    items.push({
      tone: growth >= 0 ? 'good' : 'bad',
      icon: growth >= 0 ? 'trend_up' : 'trend_down',
      title: `${growth >= 0 ? 'Up' : 'Down'} ${fmt.percent(Math.abs(growth))} on the previous ${days} days`,
      text: `${fmt.int(totals.chats)} conversations, compared with ${fmt.int(previous.chats)}.`,
    });
  }
  const peakHour = data.hourly.indexOf(Math.max(...data.hourly));
  const dayTotals = data.weekday_hourly.map((row) => row.reduce((sum, value) => sum + value, 0));
  const peakDay = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][dayTotals.indexOf(Math.max(...dayTotals))];
  items.push({ icon: 'clock', title: `Busiest around ${fmt.hour(peakHour)} on ${peakDay}s`, text: 'Plan updates and maintenance away from this time.' });
  if (data.keywords.length) {
    items.push({ icon: 'bulb', title: `Top topic: "${data.keywords[0].label}"`, text: `Mentioned in ${fmt.int(data.keywords[0].count)} conversations. Make sure the website covers it well.` });
  }
  const noMatchShare = totals.fallback / totals.chats;
  if (noMatchShare >= 0.05) {
    items.push({ tone: 'bad', icon: 'alert', title: `${fmt.percent(noMatchShare)} of questions found no matching content`, text: 'Check Conversations for these questions and add the missing information to the website.' });
  }
  if (totals.failed) {
    items.push({ tone: 'bad', icon: 'alert', title: `${fmt.int(totals.failed)} conversations failed`, text: data.errors[0] ? `Most common error: ${data.errors[0].label}` : 'See Models & health.' });
  }
  if (data.feedback.count) {
    const low = (data.feedback.distribution['1'] || 0) + (data.feedback.distribution['2'] || 0);
    items.push({
      tone: data.feedback.average_rating >= 4 ? 'good' : low ? 'bad' : '',
      icon: 'star',
      title: `Visitors rate the bot ${data.feedback.average_rating.toFixed(1)} out of 5`,
      text: low ? `${fmt.int(low)} low ratings (1-2). Read them in Feedback.` : 'No low ratings this period.',
    });
  }
  return h('div', { class: 'insights' }, items.slice(0, 5).map((item) => h('div', { class: 'insight' },
    h('div', { class: `icon ${item.tone || ''}` }, icon(item.icon)),
    h('div', {}, h('strong', {}, item.title), h('span', {}, item.text)))));
}

// ---------- Usage & cost ----------
async function renderUsage(content) {
  const days = state.days || state.settings.defaultDays;
  const data = await getAnalytics(days);
  const { totals } = data;
  const currency = state.settings.currency;
  const heroValue = h('span', { class: 'value' });
  const projected = h('strong', {});
  const perChat = h('strong', {});
  const breakdown = h('dl', { class: 'kv' });

  const refresh = () => {
    const cost = costOf(totals, days);
    countUp(heroValue, cost.total, (value) => fmt.money(value, currency));
    projected.textContent = fmt.money((cost.total / days) * 30, currency);
    perChat.textContent = totals.chats ? fmt.money(cost.tokens / totals.chats, currency) : '-';
    breakdown.replaceChildren(
      h('dt', {}, 'Model tokens'), h('dd', {}, fmt.money(cost.tokens, currency)),
      h('dt', {}, 'Fixed (hosting, monitoring)'), h('dd', {}, fmt.money(cost.fixed, currency)),
      h('dt', {}, 'Input tokens'), h('dd', {}, `${fmt.compact(totals.input_tokens)} at ${fmt.money(state.settings.inputPrice, currency)} per million`),
      h('dt', {}, 'Output tokens'), h('dd', {}, `${fmt.compact(totals.output_tokens)} at ${fmt.money(state.settings.outputPrice, currency)} per million`));
  };

  const priceInput = (key, label, hint) => {
    const input = h('input', { class: 'input', type: 'number', min: '0', step: '0.01', value: String(state.settings[key]), id: `price-${key}` });
    input.addEventListener('input', () => {
      const value = Number.parseFloat(input.value);
      if (Number.isFinite(value) && value >= 0) {
        saveSettings({ [key]: value });
        refresh();
      }
    });
    return h('div', { class: 'field' }, h('label', { for: input.id }, label), input, h('span', { class: 'hint' }, hint));
  };

  const generationCalls = totals.completed - totals.fallback + totals.failed;
  const tokenLabels = data.daily.map((day) => fmt.shortDay(day.date));
  const tokenSeries = [
    { label: 'Input tokens', color: token('--series-1'), values: data.daily.map((day) => day.input_tokens) },
    { label: 'Output tokens', color: token('--series-2'), values: data.daily.map((day) => day.output_tokens) },
  ];
  const tokensPanel = chartPanel({
    size: 'tall',
    series: tokenSeries,
    table: { caption: 'Tokens per day', headers: ['Day', 'Input tokens', 'Output tokens'], rows: data.daily.map((day, index) => [tokenLabels[index], fmt.int(day.input_tokens), fmt.int(day.output_tokens)]) },
    draw: (canvas) => barChart(canvas, { labels: tokenLabels, stacked: true, datasets: tokenSeries.map((item) => ({ label: item.label, data: item.values, backgroundColor: item.color })) }),
  });

  const dailyColumns = [
    { key: 'date', label: 'Day', render: (row) => fmt.date(`${row.date}T12:00:00+08:00`) },
    { key: 'chats', label: 'Conversations', align: 'right', render: (row) => fmt.int(row.chats) },
    { key: 'sessions', label: 'Sessions', align: 'right', render: (row) => fmt.int(row.sessions) },
    { key: 'input_tokens', label: 'Input tokens', align: 'right', render: (row) => fmt.int(row.input_tokens) },
    { key: 'output_tokens', label: 'Output tokens', align: 'right', render: (row) => fmt.int(row.output_tokens) },
    { key: 'cost', label: 'Token cost', align: 'right', sortValue: (row) => row.input_tokens + row.output_tokens, render: (row) => fmt.money(costOf(row, 0).tokens, currency), csv: (row) => costOf(row, 0).tokens.toFixed(4) },
  ];
  const dailyRows = [...data.daily].reverse();

  content.replaceChildren(
    pageHead('Usage & cost', 'Token use and estimated spend. Prices are saved in this browser.', periodControl(content, renderUsage),
      h('button', { class: 'button', onclick: () => download(`snaic-usage-${days}d.csv`, toCsv(dailyColumns, dailyRows)) }, icon('download'), 'Export CSV')),
    h('div', { class: 'grid two stagger' },
      h('section', { class: 'card' },
        h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, 'Estimated cost'), h('p', {}, `Last ${days} days`))),
        h('div', { class: 'hero' }, heroValue),
        h('div', { class: 'grid halves' },
          h('div', {}, h('div', { class: 'muted' }, 'Projected per month'), projected),
          h('div', {}, h('div', { class: 'muted' }, 'Model cost per conversation'), perChat)),
        breakdown),
      card('Cost calculator', 'Prices per million tokens', h('div', { class: 'grid' },
        priceInput('inputPrice', `Input price (${currency})`, 'Prompt, retrieved content and question'),
        priceInput('outputPrice', `Output price (${currency})`, 'The answer'),
        priceInput('monthlyFixedCost', `Fixed monthly cost (${currency})`, 'Hosting and monitoring you want included'),
        h('div', { class: 'callout' }, `Token counts are estimates: about 4 characters per token. ${fmt.percent(data.tokens_measured_share)} of these conversations recorded their exact prompt size; the rest use a typical size.`)))),
    h('div', { class: 'grid kpis stagger' },
      kpi('Generation calls', 'chip', generationCalls, { sub: 'Answers written by the language model' }),
      kpi('Search requests', 'search', totals.chats, { sub: 'Embedding and reranking, one each per conversation' }),
      kpi('Input tokens', 'usage', totals.input_tokens, { delta: change(totals.input_tokens, data.previous_totals.input_tokens), invert: true }),
      kpi('Output tokens', 'usage', totals.output_tokens, { delta: change(totals.output_tokens, data.previous_totals.output_tokens), invert: true }),
      kpi('Average answer', 'chat', data.average_answer_chars, { format: (value) => `${fmt.int(value)} chars` })),
    h('div', { class: 'grid stagger' },
      card('Tokens per day', 'Input and output, estimated', tokensPanel, { actions: [tokensPanel.toggle] }),
      card('Models used', 'Conversations per generation model', rankList(data.models, 'No model calls yet.')),
      h('section', { class: 'card flush' }, h('div', { class: 'card-head pad' }, h('div', {}, h('h2', {}, 'Daily breakdown'))), dataTable({ columns: dailyColumns, rows: dailyRows, pageSize: state.settings.pageSize })),
    ),
  );
  refresh();
}

// ---------- Conversations ----------
const STATUS_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'answered', label: 'Answered' },
  { value: 'fallback', label: 'No match' },
  { value: 'failed', label: 'Failed' },
];

function statusOf(row) {
  if (row.status === 'failed') return { key: 'failed', label: 'Failed', tone: 'bad' };
  if (row.used_fallback) return { key: 'fallback', label: 'No match', tone: 'warn' };
  return { key: 'answered', label: 'Answered', tone: 'good' };
}

async function renderConversations(content, params) {
  const filters = { q: params.get('q') || '', status: 'all', range: '30' };
  const tableHolder = h('section', { class: 'card flush' });
  let rows = [];

  const load = async () => {
    tableHolder.classList.add('loading');
    const startAt = filters.range === 'all' ? '' : new Date(Date.now() - Number(filters.range) * 86400000).toISOString();
    const result = await api.activity({ limit: 500, keyword: filters.q, startAt });
    rows = result.activities;
    draw();
    tableHolder.classList.remove('loading');
  };

  const columns = [
    { key: 'created_at', label: 'Time', render: (row) => h('span', { title: fmt.dateTime(row.created_at) }, fmt.relative(row.created_at)), csv: (row) => row.created_at },
    { key: 'request_message', label: 'Question', clip: true },
    { key: 'status', label: 'Status', sortValue: (row) => statusOf(row).key, render: (row) => { const item = statusOf(row); return h('span', { class: `pill ${item.tone}` }, item.label); }, csv: (row) => statusOf(row).label },
    { key: 'duration', label: 'Response', align: 'right', sortValue: (row) => row.metadata?.duration_ms ?? null, render: (row) => fmt.ms(row.metadata?.duration_ms), csv: (row) => row.metadata?.duration_ms ?? '' },
    { key: 'model', label: 'Model', clip: true, render: (row) => (row.model || '-').split('/').pop() },
  ];

  const draw = () => {
    const visible = filters.status === 'all' ? rows : rows.filter((row) => statusOf(row).key === filters.status);
    tableHolder.replaceChildren(dataTable({
      columns,
      rows: visible,
      sort: { key: 'created_at', dir: 'desc' },
      pageSize: state.settings.pageSize,
      onRowClick: (row) => showConversation(row, rows),
      emptyTitle: 'No conversations',
      emptyText: 'Try a wider date range or a different search.',
    }));
    tableHolder.exportRows = visible;
  };

  const chips = h('div', { class: 'toolbar', role: 'group', 'aria-label': 'Status' }, STATUS_FILTERS.map((item) => h('button', {
    class: 'chip',
    type: 'button',
    'aria-pressed': String(item.value === filters.status),
    onclick: (event) => {
      filters.status = item.value;
      chips.querySelectorAll('.chip').forEach((chip) => chip.setAttribute('aria-pressed', String(chip === event.currentTarget)));
      draw();
    },
  }, item.label)));

  content.replaceChildren(
    pageHead('Conversations', 'Every question visitors asked SNAIC Bot, with the answer it gave.',
      h('button', { class: 'button', onclick: () => download('snaic-conversations.csv', toCsv([...columns, { key: 'response_answer', label: 'Answer' }, { key: 'session_id', label: 'Session' }], tableHolder.exportRows || [])) }, icon('download'), 'Export CSV')),
    h('div', { class: 'filters' },
      h('div', { class: 'search' }, searchBox('Search questions and answers', filters.q, (value) => { filters.q = value; load().catch((exc) => toast(exc.message, 'bad')); })),
      select([{ value: '1', label: 'Today' }, { value: '7', label: 'Last 7 days' }, { value: '30', label: 'Last 30 days' }, { value: '90', label: 'Last 90 days' }, { value: 'all', label: 'All time' }], filters.range, (value) => { filters.range = value; load().catch((exc) => toast(exc.message, 'bad')); }, 'Date range'),
      chips),
    tableHolder,
  );
  await load();
}

function showConversation(row, allRows) {
  const item = statusOf(row);
  const thread = allRows
    .filter((other) => row.session_id && other.session_id === row.session_id)
    .sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
  const turns = (thread.length ? thread : [row]).flatMap((turn) => [
    h('div', { class: 'bubble user' }, turn.request_message),
    turn.response_answer ? h('div', { class: 'bubble bot' }, turn.response_answer) : h('div', { class: 'bubble bot muted' }, turn.error_message || 'No answer'),
  ]);
  openDrawer('Conversation', [
    h('div', { class: 'toolbar' }, h('span', { class: `pill ${item.tone}` }, item.label), h('span', { class: 'muted' }, fmt.dateTime(row.created_at))),
    h('div', { class: 'grid' }, turns),
    h('dl', { class: 'kv' },
      h('dt', {}, 'Turns in session'), h('dd', {}, String(Math.max(1, thread.length))),
      h('dt', {}, 'Response time'), h('dd', {}, fmt.ms(row.metadata?.duration_ms)),
      h('dt', {}, 'Model'), h('dd', {}, row.model || '-'),
      h('dt', {}, 'Sources used'), h('dd', {}, String(row.citations_count)),
      h('dt', {}, 'Session'), h('dd', {}, row.session_id || '-'),
      row.error_message ? [h('dt', {}, 'Error'), h('dd', {}, row.error_message)] : ''),
  ]);
}

// ---------- Feedback ----------
async function renderFeedback(content) {
  const items = await api.feedback({ limit: 500 });
  const filters = { rating: 'all', q: '', sort: 'newest' };
  const list = h('div', { class: 'feedback-list stagger' });
  const ratings = items.map((item) => item.rating);
  const average = ratings.length ? ratings.reduce((sum, value) => sum + value, 0) / ratings.length : null;
  const positive = ratings.length ? ratings.filter((value) => value >= 4).length / ratings.length : 0;

  const draw = () => {
    let visible = items.filter((item) => filters.rating === 'all' || item.rating === Number(filters.rating));
    if (filters.q) visible = visible.filter((item) => (item.comments || '').toLowerCase().includes(filters.q.toLowerCase()));
    const sorters = {
      newest: (a, b) => new Date(b.created_at) - new Date(a.created_at),
      oldest: (a, b) => new Date(a.created_at) - new Date(b.created_at),
      lowest: (a, b) => a.rating - b.rating,
      highest: (a, b) => b.rating - a.rating,
    };
    visible = [...visible].sort(sorters[filters.sort]);
    list.replaceChildren(...(visible.length ? visible.map(feedbackCard) : [empty(items.length ? 'No matches' : 'No feedback yet', items.length ? 'Try another filter.' : 'Feedback appears when visitors rate a chat.')]));
  };

  const chips = h('div', { class: 'toolbar', role: 'group', 'aria-label': 'Rating' }, ['all', '5', '4', '3', '2', '1'].map((value) => h('button', {
    class: 'chip', type: 'button', 'aria-pressed': String(value === filters.rating),
    onclick: (event) => {
      filters.rating = value;
      chips.querySelectorAll('.chip').forEach((chip) => chip.setAttribute('aria-pressed', String(chip === event.currentTarget)));
      draw();
    },
  }, value === 'all' ? 'All' : `${value}★`)));

  content.replaceChildren(
    pageHead('Feedback', 'Ratings and comments visitors left after chatting.'),
    h('div', { class: 'grid kpis stagger' },
      kpi('Average rating', 'star', average ?? '-', { format: (value) => value.toFixed(2) }),
      kpi('Ratings', 'chat', items.length),
      kpi('Positive (4-5★)', 'check', positive, { format: (value) => fmt.percent(value) })),
    h('div', { class: 'filters' },
      h('div', { class: 'search' }, searchBox('Search comments', '', (value) => { filters.q = value; draw(); })),
      select([{ value: 'newest', label: 'Newest first' }, { value: 'oldest', label: 'Oldest first' }, { value: 'lowest', label: 'Lowest rating' }, { value: 'highest', label: 'Highest rating' }], filters.sort, (value) => { filters.sort = value; draw(); }, 'Sort'),
      chips),
    list,
  );
  draw();
}

function feedbackCard(item) {
  const open = () => openDrawer('Feedback', [
    h('div', { class: 'toolbar' }, starRating(item.rating), h('span', { class: 'muted' }, fmt.dateTime(item.created_at))),
    item.comments ? h('div', { class: 'callout' }, item.comments) : '',
    h('h3', {}, 'Conversation'),
    h('div', { class: 'bubble bot' }, item.full_chat_text || 'The conversation was not recorded.'),
  ]);
  return h('article', { class: 'card feedback-card', tabindex: '0', onclick: open, onkeydown: (event) => { if (event.key === 'Enter') open(); } },
    starRating(item.rating),
    h('p', {}, item.comments || 'No comment'),
    h('div', { class: 'meta' }, h('span', {}, fmt.relative(item.created_at)), h('span', {}, 'View conversation')));
}

// ---------- Models & health ----------
async function renderModels(content) {
  const [selection, catalog, health, modelHealth] = await Promise.all([
    api.modelSelection(), api.modelCatalog(), api.health(), api.modelHealth().catch((exc) => ({ status: 'error', error: exc.message })),
  ]);

  const modelCard = (title, probe, fallbackModel, extra) => {
    const ok = probe?.ok;
    return h('section', { class: 'card' },
      h('div', { class: 'card-head' }, h('div', {}, h('h2', {}, title), h('p', {}, probe?.provider ? `Provider: ${probe.provider.toUpperCase()}` : '')),
        probe ? h('span', { class: `pill ${ok ? 'good' : 'bad'}` }, ok ? 'Responding' : 'Not responding') : h('span', { class: 'pill neutral' }, 'Off')),
      h('dl', { class: 'kv' },
        h('dt', {}, 'Model'), h('dd', {}, probe?.model || fallbackModel || '-'),
        h('dt', {}, 'Latency'), h('dd', {}, fmt.ms(probe?.latency_ms)),
        h('dt', {}, 'Status'), h('dd', {}, probe?.detail || '-'),
        extra || ''));
  };

  const dependency = (name, item, text) => h('div', { class: 'status-row' },
    h('div', { class: 'what' }, h('strong', {}, name), h('span', {}, text || item?.detail || '')),
    h('span', { class: `pill ${item?.ok ? 'good' : 'bad'}` }, item?.ok ? 'Healthy' : 'Unhealthy'));

  const providers = Object.entries(health.providers || {}).filter(([, item]) => item.in_use);
  const inUse = new Set([selection.generation_profile, selection.embedding_profile]);
  const catalogRows = [
    ...catalog.generation_profiles.map((item) => ({ ...item, kind: 'Generation' })),
    ...catalog.embedding_profiles.map((item) => ({ ...item, kind: 'Embedding' })),
  ];

  content.replaceChildren(
    pageHead('Models & health', modelHealth.checked_at ? `Last model check ${fmt.relative(modelHealth.checked_at)}. Models are checked once a day, and again after an error.` : 'Live status of the AI models and services.',
      h('button', { class: 'button', onclick: () => route() }, icon('refresh'), 'Check again')),
    h('div', { class: 'grid thirds stagger' },
      modelCard('Answer generation', modelHealth.generation, selection.generation_model, [h('dt', {}, 'Profile'), h('dd', {}, selection.generation_profile)]),
      modelCard('Embeddings (search)', modelHealth.embedding, selection.embedding_model, [h('dt', {}, 'Dimensions'), h('dd', {}, String(selection.embedding_dimension))]),
      modelCard('Reranker', modelHealth.rerank, null)),
    h('div', { class: 'grid halves stagger' },
      card('Services', health.status === 'ok' ? 'All services are healthy' : 'Some services need attention', h('div', { class: 'status-list' },
        dependency('PostgreSQL', health.postgres, 'Conversations, users, feedback and documents'),
        dependency('Redis', health.redis, 'Sessions, caching and rate limits'),
        dependency('Qdrant', health.qdrant, 'Search index of the knowledge base'),
        providers.map(([name, item]) => dependency(`${name.toUpperCase()} API`, item, 'Model provider in use')))),
      h('section', { class: 'card flush' },
        h('div', { class: 'card-head pad' }, h('div', {}, h('h2', {}, 'Available model profiles'), h('p', {}, 'Profiles the backend can switch to'))),
        dataTable({
          columns: [
            { key: 'profile_name', label: 'Profile', render: (row) => h('span', {}, row.profile_name, inUse.has(row.profile_name) ? h('span', { class: 'pill good' }, 'In use') : '') },
            { key: 'kind', label: 'Type' },
            { key: 'model', label: 'Model', clip: true },
          ],
          rows: catalogRows,
          sort: { key: 'kind', dir: 'desc' },
          pageSize: 8,
        }))),
  );
}

// ---------- Knowledge base ----------
async function renderKnowledge(content) {
  const documents = (await api.documents()).map((item) => item.document);
  const filters = { q: '', source: 'all' };
  const counts = documents.reduce((map, doc) => map.set(doc.source_type, (map.get(doc.source_type) || 0) + 1), new Map());
  const sources = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const holder = h('section', { class: 'card flush' });
  const columns = [
    { key: 'title', label: 'Title', clip: true, render: (row) => (row.url ? h('a', { href: row.url, target: '_blank', rel: 'noopener noreferrer' }, row.title) : row.title) },
    { key: 'source_type', label: 'Source', render: (row) => h('span', { class: 'pill neutral' }, sourceLabel(row.source_type)) },
    { key: 'updated_at', label: 'Updated', render: (row) => fmt.date(row.updated_at) },
    { key: 'created_by', label: 'Added by' },
  ];
  const draw = () => {
    const rows = filters.source === 'all' ? documents : documents.filter((doc) => doc.source_type === filters.source);
    holder.replaceChildren(dataTable({ columns, rows, search: filters.q, searchKeys: ['title', 'url'], sort: { key: 'updated_at', dir: 'desc' }, pageSize: state.settings.pageSize, emptyTitle: 'No documents' }));
  };

  const refreshButton = h('button', {
    class: 'button primary',
    onclick: () => openModal({
      title: 'Refresh website content?',
      text: 'Re-reads projects, news and team members from Contentful and replaces the indexed copies. Takes about a minute; the chatbot keeps working meanwhile.',
      confirmLabel: 'Refresh',
      onConfirm: async () => {
        toast('Refreshing website content…');
        const result = await api.refreshCms();
        toast(`Refreshed: ${result.succeeded} documents updated${result.failed ? `, ${result.failed} failed` : ''}`, result.failed ? 'bad' : '');
        route();
      },
    }),
  }, icon('refresh'), 'Refresh website content');

  const sourcePanel = chartPanel({
    size: 'short',
    table: { caption: 'Documents by source', headers: ['Source', 'Documents'], rows: sources.map(([name, count]) => [sourceLabel(name), fmt.int(count)]) },
    draw: (canvas) => barChart(canvas, { labels: sources.map(([name]) => sourceLabel(name)), horizontal: true, datasets: [{ label: 'Documents', data: sources.map(([, count]) => count), backgroundColor: token('--series-1') }] }),
  });

  content.replaceChildren(
    pageHead('Knowledge base', 'What SNAIC Bot knows: website content and uploaded documents.', refreshButton),
    h('div', { class: 'grid two stagger' },
      h('div', { class: 'grid kpis' },
        kpi('Documents', 'book', documents.length),
        kpi('Website (CMS) entries', 'overview', counts.get('cms') || 0),
        kpi('Sources', 'table', sources.length)),
      card('By source', '', sourcePanel, { actions: [sourcePanel.toggle] })),
    h('div', { class: 'filters' },
      h('div', { class: 'search' }, searchBox('Search titles', '', (value) => { filters.q = value; draw(); })),
      select([{ value: 'all', label: 'All sources' }, ...sources.map(([name]) => ({ value: name, label: sourceLabel(name) }))], 'all', (value) => { filters.source = value; draw(); }, 'Source')),
    holder,
  );
  draw();
}

function sourceLabel(type) {
  return { cms: 'Website (CMS)', website: 'Web page', text: 'Text', pdf: 'PDF', docx: 'Word', xlsx: 'Excel', csv: 'CSV', txt: 'Text file' }[type] || type;
}

// ---------- Users ----------
async function renderUsers(content) {
  const users = await api.users();
  const holder = h('section', { class: 'card flush' });
  let search = '';

  const act = async (work, message) => {
    try {
      await work();
      toast(message);
      route();
    } catch (exc) {
      toast(exc.message, 'bad');
    }
  };

  const rowActions = (user) => {
    const self = user.id === state.user.id;
    return h('div', { class: 'toolbar' },
      h('button', {
        class: 'button ghost', disabled: self, title: self ? 'You cannot disable your own account' : '',
        onclick: (event) => { event.stopPropagation(); act(() => api.updateUser(user.id, { is_active: !user.is_active }), user.is_active ? `${user.username} disabled` : `${user.username} enabled`); },
      }, user.is_active ? 'Disable' : 'Enable'),
      h('button', {
        class: 'button ghost',
        onclick: (event) => { event.stopPropagation(); resetPassword(user); },
      }, 'Reset password'),
      h('button', {
        class: 'button icon ghost', disabled: self, 'aria-label': `Delete ${user.username}`,
        onclick: (event) => {
          event.stopPropagation();
          openModal({
            title: `Delete ${user.username}?`,
            text: 'The account and its chat history are removed. This cannot be undone.',
            confirmLabel: 'Delete', danger: true,
            onConfirm: async () => { await api.deleteUser(user.id); toast(`${user.username} deleted`); route(); },
          });
        },
      }, icon('trash')));
  };

  const draw = () => holder.replaceChildren(dataTable({
    columns: [
      { key: 'username', label: 'User', render: (user) => h('span', { class: 'toolbar' }, h('span', { class: 'avatar', 'aria-hidden': 'true' }, user.username.slice(0, 2).toUpperCase()), user.username, user.id === state.user.id ? h('span', { class: 'pill neutral' }, 'You') : '') },
      { key: 'is_admin', label: 'Role', render: (user) => (user.is_admin ? 'Administrator' : 'Service / API') },
      { key: 'is_active', label: 'Status', render: (user) => h('span', { class: `pill ${user.is_active ? 'good' : 'neutral'}` }, user.is_active ? 'Active' : 'Disabled') },
      { key: 'created_at', label: 'Created', render: (user) => fmt.date(user.created_at) },
      { key: 'actions', label: '', sortable: false, render: rowActions },
    ],
    rows: users,
    search,
    searchKeys: ['username'],
    sort: { key: 'created_at', dir: 'desc' },
    pageSize: state.settings.pageSize,
  }));

  const addUser = () => {
    const admin = h('input', { type: 'checkbox', name: 'is_admin', checked: true });
    openModal({
      title: 'Add user',
      text: 'Administrators can use this console. Service accounts can only call the API, like the website chatbot.',
      confirmLabel: 'Add user',
      body: [
        h('div', { class: 'field' }, h('label', { for: 'new-user' }, 'Username'), h('input', { class: 'input', id: 'new-user', name: 'username', required: true, autocomplete: 'off' })),
        h('div', { class: 'field' }, h('label', { for: 'new-pass' }, 'Password'), h('input', { class: 'input', id: 'new-pass', name: 'password', type: 'password', required: true, minlength: '8', autocomplete: 'new-password' }), h('span', { class: 'hint' }, 'At least 8 characters. Share it with the person securely.')),
        h('label', { class: 'check-row' }, 'Administrator', h('span', { class: 'switch' }, admin, h('span', {}))),
      ],
      onConfirm: async (form) => {
        await api.createUser({ username: form.get('username'), password: form.get('password'), is_admin: admin.checked, is_active: true });
        toast('User added');
        route();
      },
    });
  };

  const resetPassword = (user) => openModal({
    title: `New password for ${user.username}`,
    text: user.is_admin ? '' : 'If the website chatbot uses this account, update its password in Amplify too, or the chatbot stops working.',
    confirmLabel: 'Set password',
    body: [h('div', { class: 'field' }, h('label', { for: 'reset-pass' }, 'New password'), h('input', { class: 'input', id: 'reset-pass', name: 'password', type: 'password', required: true, minlength: '8', autocomplete: 'new-password' }))],
    onConfirm: async (form) => { await api.updateUser(user.id, { password: form.get('password') }); toast('Password updated'); },
  });

  content.replaceChildren(
    pageHead('Users', 'Accounts are invite-only: add people here.', h('button', { class: 'button primary', onclick: addUser }, icon('plus'), 'Add user')),
    h('div', { class: 'callout spaced' }, 'The website chatbot signs in with its own service account. Disabling it or changing its password stops the chatbot until Amplify is updated.'),
    h('div', { class: 'filters' }, h('div', { class: 'search' }, searchBox('Search users', '', (value) => { search = value; draw(); }))),
    holder,
  );
  draw();
}

// ---------- Settings ----------
async function renderSettings(content) {
  const setting = (title, text, control) => h('div', { class: 'setting' }, h('div', { class: 'what' }, h('strong', {}, title), h('span', {}, text)), h('div', { class: 'control' }, control));
  const number = (key) => {
    const input = h('input', { class: 'input', type: 'number', min: '0', step: '0.01', value: String(state.settings[key]), 'aria-label': key });
    input.addEventListener('change', () => {
      const value = Number.parseFloat(input.value);
      if (Number.isFinite(value) && value >= 0) { saveSettings({ [key]: value }); state.analytics.clear(); toast('Saved'); }
    });
    return input;
  };

  content.replaceChildren(
    pageHead('Settings', 'Saved in this browser.'),
    h('div', { class: 'grid halves stagger' },
      card('Appearance', '', h('div', { class: 'settings-section' },
        setting('Theme', 'Follow the system, or choose light or dark.', segmented(
          [{ value: 'system', label: 'System' }, { value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }],
          state.settings.theme, (value) => { saveSettings({ theme: value }); applyTheme(); }, 'Theme')),
        setting('Default period', 'Used by Overview and Usage & cost.', select(PERIODS.map((item) => ({ value: String(item.value), label: item.label })), String(state.settings.defaultDays), (value) => { saveSettings({ defaultDays: Number(value) }); state.days = Number(value); toast('Saved'); }, 'Default period')),
        setting('Rows per page', 'In tables.', select(['10', '25', '50', '100'].map((value) => ({ value, label: value })), String(state.settings.pageSize), (value) => { saveSettings({ pageSize: Number(value) }); toast('Saved'); }, 'Rows per page')))),
      card('Cost', 'Used for every cost estimate', h('div', { class: 'settings-section' },
        setting('Currency', 'Shown on prices and totals.', select([{ value: 'US$', label: 'US$' }, { value: 'S$', label: 'S$' }, { value: '€', label: '€' }], state.settings.currency, (value) => { saveSettings({ currency: value }); toast('Saved'); }, 'Currency')),
        setting('Input price', 'Per million input tokens.', number('inputPrice')),
        setting('Output price', 'Per million output tokens.', number('outputPrice')),
        setting('Fixed monthly cost', 'Hosting and monitoring to include.', number('monthlyFixedCost')))),
      card('Account', '', h('div', { class: 'settings-section' },
        setting('Signed in as', state.user.username, h('button', { class: 'button', onclick: signOut }, icon('logout'), 'Sign out')),
        setting('Session', 'Sign-ins last one hour, or until this tab closes.', h('span', { class: 'pill good' }, 'Active')))),
      card('About', '', h('dl', { class: 'kv' },
        h('dt', {}, 'API'), h('dd', {}, location.origin),
        h('dt', {}, 'API reference'), h('dd', {}, h('a', { href: '/docs', target: '_blank', rel: 'noopener noreferrer' }, 'Open /docs')),
        h('dt', {}, 'Logo'), h('dd', {}, 'Placeholder. Replace app/dashboard/assets/logo.svg with the final logo.'),
        h('dt', {}, 'Reset'), h('dd', {}, h('button', { class: 'button ghost', onclick: () => { saveSettings({ ...DEFAULT_SETTINGS }); applyTheme(); route(); toast('Settings reset'); } }, 'Restore defaults'))))),
  );
}

const LOGIN_ILLUSTRATION = `<svg class="illustration" viewBox="0 0 520 360" fill="none" aria-hidden="true">
  <rect x="20" y="30" width="300" height="200" rx="22" fill="rgba(255,255,255,0.06)" stroke="rgba(255,255,255,0.12)"/>
  <rect x="44" y="56" width="90" height="10" rx="5" fill="rgba(255,255,255,0.5)"/>
  <rect x="44" y="76" width="140" height="22" rx="6" fill="rgba(255,255,255,0.85)"/>
  <path d="M44 196 C90 180 110 150 150 158 C190 166 200 120 240 112 C270 106 284 90 296 84" stroke="#3987e5" stroke-width="3" stroke-linecap="round"/>
  <path d="M44 196 C90 180 110 150 150 158 C190 166 200 120 240 112 C270 106 284 90 296 84 V206 H44 Z" fill="#3987e5" opacity="0.15"/>
  <circle cx="296" cy="84" r="6" fill="#3987e5" stroke="#0b1220" stroke-width="3"/>
  <rect x="250" y="150" width="250" height="180" rx="22" fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.14)"/>
  <rect x="276" y="270" width="18" height="34" rx="4" fill="#3987e5"/>
  <rect x="304" y="246" width="18" height="58" rx="4" fill="#3987e5"/>
  <rect x="332" y="222" width="18" height="82" rx="4" fill="#3987e5"/>
  <rect x="360" y="236" width="18" height="68" rx="4" fill="#3987e5"/>
  <rect x="388" y="204" width="18" height="100" rx="4" fill="#ff6a2b"/>
  <rect x="416" y="226" width="18" height="78" rx="4" fill="#3987e5"/>
  <rect x="444" y="250" width="18" height="54" rx="4" fill="#3987e5"/>
  <rect x="276" y="176" width="70" height="9" rx="4.5" fill="rgba(255,255,255,0.5)"/>
  <g transform="translate(60 250)">
    <rect width="150" height="80" rx="18" fill="rgba(255,255,255,0.08)" stroke="rgba(255,255,255,0.14)"/>
    <circle cx="36" cy="40" r="20" fill="#1f2937"/>
    <rect x="25" y="32" width="22" height="14" rx="7" fill="#ffffff"/>
    <rect x="29" y="36" width="4" height="6" rx="2" fill="#ff6a2b"/>
    <rect x="39" y="36" width="4" height="6" rx="2" fill="#ff6a2b"/>
    <rect x="66" y="28" width="62" height="8" rx="4" fill="rgba(255,255,255,0.6)"/>
    <rect x="66" y="44" width="44" height="8" rx="4" fill="rgba(255,255,255,0.3)"/>
  </g>
</svg>`;

start();
