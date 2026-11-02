// API client and per-browser settings for the console.
// The access token lives in sessionStorage: it ends with the browser tab and
// expires on the server after AUTH_ACCESS_TOKEN_TTL_SECONDS (one hour).

const TOKEN_KEY = 'snaic-console-token';
const SETTINGS_KEY = 'snaic-console-settings';

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function storage(kind) {
  try {
    return kind === 'session' ? window.sessionStorage : window.localStorage;
  } catch {
    return null;
  }
}

export const session = {
  get token() { return storage('session')?.getItem(TOKEN_KEY) || ''; },
  set token(value) {
    const store = storage('session');
    if (!store) return;
    if (value) store.setItem(TOKEN_KEY, value);
    else store.removeItem(TOKEN_KEY);
  },
  onExpired: () => {},
};

async function request(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && session.token) headers.Authorization = `Bearer ${session.token}`;
  let response;
  try {
    response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError('Cannot reach the server. Check your connection.', 0);
  }
  if (response.status === 401 && auth) {
    session.token = '';
    session.onExpired();
    throw new ApiError('Your session has ended. Please sign in again.', 401);
  }
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((item) => item.msg).join('; ') : `Request failed (${response.status}).`;
    throw new ApiError(message, response.status);
  }
  return payload;
}

export const api = {
  signIn: (username, password) => request('/auth/token', { method: 'POST', body: { username, password }, auth: false }),
  me: () => request('/auth/me'),
  analytics: (days) => request(`/admin/analytics?days=${days}`),
  activity: ({ limit = 500, keyword = '', startAt = '', endAt = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (keyword) params.set('keyword', keyword);
    if (startAt) params.set('start_at', startAt);
    if (endAt) params.set('end_at', endAt);
    return request(`/admin/chat-activity?${params}`);
  },
  feedback: ({ limit = 500, startAt = '' } = {}) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (startAt) params.set('start_at', startAt);
    return request(`/admin/chat-feedback?${params}`);
  },
  modelSelection: () => request('/admin/model-selection'),
  modelCatalog: () => request('/admin/model-catalog'),
  health: () => request('/health', { auth: false }),
  modelHealth: () => request('/health/models', { auth: false }),
  documents: () => request('/admin/documents?limit=2000'),
  refreshCms: () => request('/ingest/cms', { method: 'POST', body: { content_types: [], force_reingest: true } }),
  systemPrompt: () => request('/admin/system-prompt'),
  users: () => request('/admin/users'),
  createUser: (user) => request('/admin/users', { method: 'POST', body: user }),
  updateUser: (id, changes) => request(`/admin/users/${encodeURIComponent(id)}`, { method: 'PATCH', body: changes }),
  deleteUser: (id) => request(`/admin/users/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};

// ---------- Settings (this browser only) ----------
export const DEFAULT_SETTINGS = {
  theme: 'system',
  defaultDays: 30,
  currency: 'US$',
  // Prices are per million tokens. NVIDIA's hosted API is free within its
  // credits, so these start at 0: enter your contracted prices to see cost.
  inputPrice: 0,
  outputPrice: 0,
  monthlyFixedCost: 4,
  pageSize: 25,
};

export const settings = {
  load() {
    try {
      return { ...DEFAULT_SETTINGS, ...JSON.parse(storage('local')?.getItem(SETTINGS_KEY) || '{}') };
    } catch {
      return { ...DEFAULT_SETTINGS };
    }
  },
  save(values) {
    try {
      storage('local')?.setItem(SETTINGS_KEY, JSON.stringify(values));
    } catch {
      // Settings then last for this visit only.
    }
  },
};
