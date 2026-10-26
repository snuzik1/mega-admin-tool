/* API-клиент MegaTool: обёртки над fetch + WebSocket с переподключением. */

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload || {};
  }
}

function buildUrl(path, params) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === "") continue;
    url.searchParams.set(key, value);
  }
  return url;
}

async function request(method, path, { params, body, timeoutMs } = {}) {
  const url = buildUrl(path, params);
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = timeoutMs && controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller ? controller.signal : undefined,
    });
  } finally {
    if (timer) clearTimeout(timer);
  }

  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }
  if (!response.ok) {
    const message = payload && payload.error ? payload.error : `HTTP ${response.status}`;
    if (response.status === 401 && !String(path).includes("/auth/login")) {
      window.dispatchEvent(new Event("megatool:unauthorized"));
    }
    throw new ApiError(message, response.status, payload);
  }
  return payload;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body, params) => request("POST", path, { params, body }),
  put: (path, body, params) => request("PUT", path, { params, body }),
  patch: (path, body, params) => request("PATCH", path, { params, body }),
  del: (path, params) => request("DELETE", path, { params }),
};

/** WebSocket с автопереподключением. Возвращает { close, send, ready } */
export function openWS(path, { onMessage, onOpen, onClose } = {}) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL(path, `${protocol}//${window.location.host}`);

  let socket = null;
  let closed = false;
  let attempt = 0;
  let ping = null;
  const client = {
    ready: false,
    send(data) {
      if (socket && socket.readyState === WebSocket.OPEN) socket.send(typeof data === "string" ? data : JSON.stringify(data));
    },
    close() {
      closed = true;
      if (ping) clearInterval(ping);
      try {
        if (socket) socket.close();
      } catch (error) {
        /* уже закрыт */
      }
    },
  };

  const connect = () => {
    if (closed) return;
    try {
      socket = new WebSocket(url);
    } catch (error) {
      schedule();
      return;
    }
    socket.onopen = () => {
      attempt = 0;
      client.ready = true;
      if (onOpen) onOpen();
    };
    socket.onmessage = (event) => {
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (onMessage) onMessage(payload);
    };
    socket.onclose = (event) => {
      client.ready = false;
      if (event && event.code === 4401) {
        closed = true;
        if (ping) clearInterval(ping);
        window.dispatchEvent(new Event("megatool:unauthorized"));
        return;
      }
      if (onClose) onClose();
      schedule();
    };
    socket.onerror = () => {
      try {
        socket.close();
      } catch (error) {
        /* ignore */
      }
    };
  };

  const schedule = () => {
    if (closed) return;
    attempt += 1;
    const delay = Math.min(8000, 500 * 2 ** Math.min(attempt, 4));
    setTimeout(connect, delay);
  };

  connect();
  return client;
}
