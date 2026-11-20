// 화면 녹화 — node record.js '<json spec>'   (Playwright, 1920x1080)
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const spec = JSON.parse(process.argv[2]);
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));
const CURSOR = `(() => { const c = document.createElement('div'); c.style.cssText = 'position:fixed;z-index:99999;width:26px;height:26px;border-radius:50%;background:rgba(194,24,43,.55);border:2px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.3);pointer-events:none;left:-50px;top:-50px;transform:translate(-50%,-50%);transition:left .6s ease,top .6s ease';
  const add = () => document.body && document.body.appendChild(c); document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', add) : add();
  window.__cursor = (x, y) => { const z = parseFloat(document.documentElement.style.zoom) || 1; c.style.left = x / z + 'px'; c.style.top = y / z + 'px'; }; })()`;
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1920, height: 1080 }, locale: 'ko-KR', recordVideo: { dir: spec.dir, size: { width: 1920, height: 1080 } } });
  const p = await ctx.newPage(); const t0 = Date.now(); const now = () => (Date.now() - t0) / 1000;
  const until = (s) => sleep(s * 1000 - (Date.now() - t0));
  const meta = { kind: spec.kind };
  const point = async (loc) => { const box = await loc.boundingBox(); if (!box) return; const x = box.x + Math.min(box.width / 2, 160), y = box.y + box.height / 2;
    await p.evaluate(([x, y]) => window.__cursor && window.__cursor(x, y), [x, y]); await sleep(700); };
  if (spec.kind === 'card') {
    await p.goto(`file://${path.resolve(__dirname, 'cards.html')}?scene=${spec.scene}&dur=${spec.total}`); await until(spec.total);
  } else if (spec.kind === 'about') {
    await p.addInitScript(CURSOR); await p.goto('https://www.ifeanalytics.com/ko/', { waitUntil: 'networkidle' });
    await p.evaluate(() => { document.documentElement.style.scrollBehavior = 'auto'; document.documentElement.style.zoom = 1.4; });
    await until(2.0);
    await p.evaluate((dur) => { const el = document.querySelector('.founder') || document.querySelector('#about'); const target = el.getBoundingClientRect().top + scrollY - 60; const from = scrollY, t0 = performance.now();
      const step = (t) => { const k = Math.min(1, (t - t0) / dur); const e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2; scrollTo(0, from + (target - from) * e); if (k < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); }, 3500);
    await until(spec.total);
  } else {
    await p.addInitScript(CURSOR); await p.goto(spec.url); await p.waitForSelector('#sample option:nth-child(2)', { state: 'attached' });
    await p.evaluate(() => { document.documentElement.style.zoom = 1.5; });
    await until(spec.tSelect); await point(p.locator('#sample')); await p.selectOption('#sample', spec.sample);
    await until(spec.tClick - 0.8); await point(p.locator('#go')); await until(spec.tClick); meta.tClick = now(); await p.click('#go');
    await p.waitForFunction(() => document.querySelectorAll('#steps li.ok').length === 3, null, { timeout: 240000 });
    meta.tDone = now(); const base = Math.max(meta.tDone + 0.6, meta.tClick + spec.W); meta.base = base;
    for (const a of spec.posts) { await until(base + a.at);
      const m = p.locator('#doc mark', { hasText: a.text }).first();
      if (await m.count()) { await m.scrollIntoViewIfNeeded(); await point(m); await m.click(); } else meta.missing = (meta.missing || []).concat(a.text); }
    await until(base + spec.tail); meta.usage = await p.evaluate(() => document.getElementById('usage').textContent);
  }
  meta.end = now(); const v = p.video(); await ctx.close(); await b.close();
  fs.renameSync(await v.path(), spec.out); fs.writeFileSync(spec.out + '.json', JSON.stringify(meta)); console.log(JSON.stringify(meta));
})();
