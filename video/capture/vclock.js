// Virtual clock: the page's time only moves when the capture script says so.
(() => {
  let vt = 0;
  const base = Date.now();
  performance.now = () => vt;
  const RealDate = Date;
  class VDate extends RealDate {
    constructor(...a) { if (a.length === 0) super(base + vt); else super(...a); }
    static now() { return base + vt; }
  }
  window.Date = VDate;
  const timers = new Map(); let tid = 1;
  window.setTimeout = (fn, ms = 0, ...args) => { const id = tid++; timers.set(id, { t: vt + Math.max(0, +ms || 0), fn, args, iv: 0 }); return id; };
  window.setInterval = (fn, ms = 0, ...args) => { const id = tid++; const iv = Math.max(1, +ms || 0); timers.set(id, { t: vt + iv, fn, args, iv }); return id; };
  window.clearTimeout = window.clearInterval = (id) => { timers.delete(id); };
  let rafs = []; let rid = 1;
  window.requestAnimationFrame = (cb) => { const id = rid++; rafs.push({ id, cb }); return id; };
  window.cancelAnimationFrame = (id) => { rafs = rafs.filter((r) => r.id !== id); };
  const seen = new WeakMap();
  window.__vt = () => vt;
  window.__advance = (ms) => {
    const target = vt + ms;
    for (let guard = 0; guard < 10000 && !window.__freeze; guard++) {
      let next = null, nid = null;
      for (const [id, t] of timers) if (t.t <= target && (!next || t.t < next.t)) { next = t; nid = id; }
      if (!next) break;
      vt = Math.max(vt, next.t);
      if (next.iv) next.t += next.iv; else timers.delete(nid);
      try { typeof next.fn === 'function' ? next.fn(...next.args) : (0, eval)(next.fn); } catch (e) { console.error(e); }
    }
    vt = target;
    const cbs = rafs; rafs = [];
    for (const r of cbs) { try { r.cb(vt); } catch (e) { console.error(e); } }
    for (const a of document.getAnimations()) {
      let s = seen.get(a);
      if (s === undefined) { s = vt - (a.currentTime || 0); seen.set(a, s); try { a.pause(); } catch {} }
      try { a.currentTime = vt - s; } catch {}
    }
  };
  // a visible pointer, since headless screenshots have none
  addEventListener('DOMContentLoaded', () => {
    const c = document.createElement('div');
    c.id = '__cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M5 3l14 8.5-6.2 1.3L16 20l-2.7 1.2-3.2-7.1L5 18.5z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647, pointerEvents: 'none', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.45))', transition: 'none' });
    document.documentElement.appendChild(c);
    addEventListener('mousemove', (e) => { c.style.left = (e.clientX - 5) + 'px'; c.style.top = (e.clientY - 3) + 'px'; }, true);
  });
})();
