export function startDesktopLifetime() {
  const client = crypto.randomUUID();
  let stopped = false;
  const message = leaving => JSON.stringify({ client, leaving });
  const heartbeat = () => {
    if (!stopped) fetch('/api/runtime/client', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: message(false), keepalive: true
    }).catch(() => {});
  };
  const leave = () => {
    stopped = true;
    navigator.sendBeacon('/api/runtime/client', new Blob([message(true)], { type: 'application/json' }));
  };
  const resume = () => { stopped = false; heartbeat(); };
  const visible = () => { if (!document.hidden) heartbeat(); };
  heartbeat();
  const timer = setInterval(heartbeat, 15000);
  window.addEventListener('pagehide', leave);
  window.addEventListener('pageshow', resume);
  document.addEventListener('visibilitychange', visible);
  return () => {
    clearInterval(timer);
    leave();
    window.removeEventListener('pagehide', leave);
    window.removeEventListener('pageshow', resume);
    document.removeEventListener('visibilitychange', visible);
  };
}
