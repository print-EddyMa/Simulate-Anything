// Frame-exact capture of the Margin app on a virtual clock.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const FPS = 30, DT = 1000 / FPS;
const [, , outDir, part] = process.argv;
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 4/3, colorScheme: 'dark' });
  await ctx.addInitScript({ path: path.join(__dirname, 'vclock.js') });
  const p = await ctx.newPage();
  p.on('pageerror', (e) => console.error('pageerror', String(e).slice(0, 200)));
  let n = 0, mx = 720, my = 405;
  const log = fs.createWriteStream(path.join(outDir, 'marks.txt'));
  const mark = (name) => { log.write(`${n} ${name}\n`); console.log(`frame ${n}: ${name}`); };
  const settle = () => p.waitForTimeout(15);
  async function step(capture = true) {
    await p.evaluate((dt) => window.__advance(dt), DT);
    await settle();
    if (capture) {
      await p.screenshot({ path: path.join(outDir, `f${String(n).padStart(5, '0')}.jpg`), type: 'jpeg', quality: 92, timeout: 180000 });
      n++;
    }
  }
  const hold = async (sec, capture = true) => { for (let i = 0; i < Math.round(sec * FPS); i++) await step(capture); };
  const ease = (t) => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  async function moveTo(x, y, sec = 0.6, capture = true) {
    const k = Math.max(1, Math.round(sec * FPS)), x0 = mx, y0 = my;
    for (let i = 1; i <= k; i++) { const e = ease(i / k); await p.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e); await step(capture); }
    mx = x; my = y;
  }
  async function center(locator) { const bb = await locator.boundingBox(); return [bb.x + bb.width / 2, bb.y + bb.height / 2]; }
  async function click(locator, capture = true) {
    const [x, y] = await center(locator);
    await moveTo(x, y, 0.7, capture); await hold(0.15, capture);
    await p.mouse.down(); await step(capture); await p.mouse.up(); await step(capture);
  }
  const waitText = async (re, maxSec, capture) => {
    for (let i = 0; i < maxSec * FPS; i++) {
      if (re.test(await p.evaluate(() => document.body.innerText))) return true;
      await step(capture);
    }
    return false;
  };

  await p.goto(part === 'things' ? 'http://localhost:8765/?pace=0' : 'http://localhost:8765/?mock=1');
  for (let i = 0; i < 60; i++) await step(false);          // boot on the virtual clock
  await p.mouse.move(mx, my);
  const prompt = p.getByPlaceholder('Describe anything you want to build');

  if (part === 'run') {
    mark('idle');
    await hold(1.2);
    await click(prompt);
    mark('typing');
    const text = 'Design a passive radar that detects small drones around a US military base. Receive-only, using the TV broadcast signals already in the air. It has to run off-grid for days, survive storms and summer heat, and spot a small quadcopter 1.5 km out.';
    for (const ch of text) { await p.keyboard.type(ch); await step(); if (ch === '.' || ch === ',') await step(); }
    await hold(0.5);
    const send = p.getByRole('button', { name: /send|submit|run|design/i }).last();
    mark('send');
    await p.keyboard.press('Enter'); await step();
    await moveTo(1100, 660, 1.0);
    mark('running');
    const ok = await waitText(/Ready to build|Not ready yet/, 90, true);
    mark(ok ? 'done' : 'timeout');
    await hold(2.5);
  } else if (part === 'sitefail') {
    await p.getByRole('listitem').first().click();
    await waitText(/1 of 5 tests pass/, 120, false);
    await hold(0.3, false);
    await p.evaluate(() => { window.__freeze = true; });   // hold the replay at the failing design
    await p.getByRole('radio', { name: /site/i }).click();
    await hold(1.5, false);
    await p.evaluate(() => { document.getElementById('__cursor').style.display = 'none'; });
    mark('site');
    await hold(5);
  } else if (part === 'things') {
    await p.evaluate(() => { document.getElementById('__cursor').style.display = 'none'; });
    await p.getByRole('listitem').filter({ hasText: 'Heat sink' }).click();
    await waitText(/Ready to build|Not ready yet/, 240, false);
    await hold(1, false);
    const files = process.env.THINGS.split(';;');
    for (const entry of files) {
      const [f, prompt, zoom] = entry.split('|');
      const json = fs.readFileSync(f, 'utf8');
      await p.getByRole('button', { name: 'Edit JSON' }).click();
      await hold(0.5, false);
      await p.getByRole('textbox', { name: 'Design JSON' }).fill(json);
      await p.getByRole('button', { name: 'Apply' }).click();
      await hold(0.5, false);
      await p.getByRole('button', { name: 'Run tests' }).click();
      for (let i = 0; i < 600; i++) {
        await step(false);
        if (i > 30 && await p.getByRole('button', { name: 'Run tests' }).isEnabled()) break;
      }
      await p.getByRole('button', { name: 'Reset view' }).click().catch(() => {});
      if (prompt) await p.locator('header form input:not([type=file])').first().fill(prompt);
      await hold(1, false);
      // move in so the object fills the view
      await p.mouse.move(560, 400);
      for (let i = 0; i < +(zoom || process.env.ZOOM || 6); i++) { await p.mouse.wheel(0, -100); await hold(0.1, false); }
      await hold(1.5, false);
      mark(path.basename(f, '.json'));
      // a slow orbit: drag across the model
      await p.mouse.move(560, 420); await p.mouse.down();
      for (let i = 0; i < 120; i++) { await p.mouse.move(560 + i * 1.6, 420); await step(); }
      await p.mouse.up();
    }
  } else {
    // fast-forward the whole run without capturing, then film the finished design
    await p.getByRole('listitem').first().click();
    await waitText(/Ready to build|Not ready yet/, 120, false);
    await hold(3, false);
    if (part === 'site') {
      mark('model');
      await hold(1.0);
      await click(p.getByRole('radio', { name: /site/i }));
      mark('site');
      await moveTo(520, 420, 1.2);
      await hold(6);
    } else if (part === 'details') {
      mark('tests');
      await hold(0.6);
      const card = p.locator('[role=tabpanel] button').first();
      await click(card);
      mark('expanded');
      await hold(1.2);
      await moveTo(520, 640, 0.6);
      for (let i = 0; i < 40; i++) { await p.mouse.wheel(0, 28); await step(); }
      mark('scrolled');
      await hold(1.5);
      for (let i = 0; i < 70; i++) { await p.mouse.wheel(0, 30); await step(); }
      mark('verification');
      await hold(2.0);
      await click(p.getByRole('tab', { name: /system/i }));
      mark('system');
      await hold(4.0);
      await click(p.getByRole('tab', { name: /parts/i }));
      mark('parts');
      await hold(2.5);
    }
  }
  log.end();
  console.log(`done: ${n} frames`);
  await b.close();
})();
