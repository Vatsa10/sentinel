import { wsUrl } from "./api";

/**
 * Reconnecting websocket with exponential backoff (1s .. 15s). Ignores
 * `{"type":"ping"}` keepalives sent by /ws/alerts (netra/api/app.py).
 * Returns a disposer that closes the socket and stops reconnecting.
 */
export function connect(
  path: string,
  onMsg: (m: Record<string, unknown>) => void,
  onState: (up: boolean) => void
) {
  let ws: WebSocket | null = null;
  let delay = 1000;
  let closed = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (closed) return;
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => {
      delay = 1000;
      onState(true);
    };
    ws.onmessage = (e) => {
      try {
        const m = JSON.parse(e.data);
        if (m.type !== "ping") onMsg(m);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onclose = () => {
      onState(false);
      if (!closed) {
        delay = Math.min(delay * 2, 15000);
        timer = setTimeout(open, delay);
      }
    };
    ws.onerror = () => ws?.close();
  };

  open();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}
