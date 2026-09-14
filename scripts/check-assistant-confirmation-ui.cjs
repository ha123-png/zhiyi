// Opt-in real API/model + production UI confirmation check on the isolated fixture.
// Run the six-turn check-assistant-confirmation-live.py first and pass its JSON file.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const base = process.argv[2];
const evidenceFile = process.argv[3];
if (!base || !/^http:\/\/127\.0\.0\.1:891[67]$/.test(base) || !evidenceFile) {
  throw new Error('Requires the isolated server 8916/8917 and a real-model journey JSON.');
}
const out = path.resolve('.local/v040-confirmation-ui');
fs.mkdirSync(out, { recursive: true });
const report = { passed: false, url: base, cases: [], errors: [] };
async function api(method, endpoint, body) {
  const res = await fetch(base + '/api/v1' + endpoint, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await res.json();
  assert(res.ok, `${endpoint}: ${JSON.stringify(value)}`);
  return value;
}
async function until(fn, timeout = 180000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await fn(); if (result) return result; await new Promise(r => setTimeout(r, 150)); }
  throw new Error('Timed out waiting for the isolated confirmation journey');
}
(async () => {
  const evidence = JSON.parse(fs.readFileSync(evidenceFile, 'utf8'));
  assert(evidence.passed, 'Start from a passing real-model journey');
  const previous = evidence.turns.at(-1).detail;
  const table = await api('GET', '/tables/ask-synthetic');
  assert.equal(table.rows.length, 6);
  const first = table.rows.slice().sort((a,b) => a.id-b.id)[0];
  const profiles = await api('GET', '/models/profiles');
  const profile = profiles.find(p => p.id === previous.profile_id);
  assert(profile && !profile.is_remote, 'This UI run uses the local fixture model');
  const response = await api('POST', '/assistant/runs', {
    text: '把第一条记录的含税总额增加1，请先给预览。', thread_id: previous.id,
    context: { table_ids: ['ask-synthetic'] }, profile_id: profile.id, profile_version: profile.version,
  });
  report.run = response;
  await until(async () => {
    const d = await api('GET', `/assistant/threads/${previous.id}`);
    const r = d.runs.find(r => r.id === response.run_id);
    if (['failed','cancelled'].includes(r.status)) throw new Error(r.error || r.status);
    return r.status === 'completed';
  });
  let detail = await api('GET', `/assistant/threads/${previous.id}`);
  const plan = detail.tools.find(t => t.status === 'pending');
  assert(plan, 'The real model must produce a real confirmation card');
  const changed = plan.result.items.flatMap(i => i.affected);
  assert.equal(changed.length, 1);
  assert.equal(changed[0].row_id, first.id);
  assert.equal(changed[0].after.total, first.values.total + 1);
  const title = '确认与撤销 · 独立页面验收 ' + previous.id.slice(0, 8);
  await api('PATCH', `/assistant/threads/${previous.id}`, { title });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  const page = await context.newPage();
  page.on('pageerror', e => report.errors.push(e.message));
  try {
    const open = async () => {
      await page.goto(base);
      await page.locator('.app-sidebar').waitFor();
      if (await page.getByRole('dialog', { name: '首次使用引导' }).isVisible()) {
        await page.getByRole('button', { name: '跳过引导', exact: true }).click();
      }
      await page.locator('.app-sidebar').getByRole('button', { name: '问知意', exact: true }).click();
      await page.locator('.ask-thread-title').filter({ hasText: title }).click();
      await page.locator('.ask-operation-card').last().waitFor();
      await page.waitForFunction(() => ![...document.querySelectorAll('.ask-status')].some(el => el.textContent === '正在准备图表…'));
    };
    await open();
    const confirm = page.getByRole('button', { name: '确认执行', exact: true });
    const approveCurrent = async () => {
      await page.waitForFunction(() => ![...document.querySelectorAll('.ask-status')].some(el => el.textContent === '正在准备图表…'));
      await Promise.all([
        page.waitForResponse(r => r.url().endsWith('/decision') && r.request().method() === 'POST'),
        confirm.click(),
      ]);
    };
    await confirm.waitFor();
    await page.waitForFunction(() => ![...document.querySelectorAll('.ask-status')].some(el => el.textContent === '正在准备图表…'));
    await confirm.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, 'preview.png') });
    await approveCurrent();
    await until(async () => (await api('GET', `/assistant/threads/${previous.id}`)).tools.find(t => t.id === plan.id)?.status === 'approved');
    assert.equal((await api('GET', '/tables/ask-synthetic')).rows.find(r => r.id === first.id).values.total, first.values.total + 1);
    await open();
    assert.equal(await confirm.count(), 0, 'Reload must not resurrect confirmation');
    const card = page.locator('.ask-operation-card').filter({ hasText: '含税总额增加 1' }).last();
    await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/undo') && r.request().method() === 'POST'),
      card.getByRole('button', { name: '撤销本次修改', exact: true }).click(),
    ]);
    await confirm.waitFor();
    await approveCurrent();
    await until(async () => (await api('GET', `/assistant/threads/${previous.id}`)).tools.find(t => t.id === plan.id)?.status === 'undone');
    assert.equal((await api('GET', '/tables/ask-synthetic')).rows.find(r => r.id === first.id).values.total, first.values.total);
    await open();
    assert.equal(await confirm.count(), 0);
    await page.locator('.ask-operation-card').last().scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, 'undone-reloaded.png') });
    assert.deepEqual(report.errors, []);
    report.cases = ['real model preview', 'UI confirm writes actual row', 'reload preserves approved state', 'UI undo restores actual value', 'reload preserves undone state'];
    report.passed = true;
  } finally {
    if (!report.passed) await page.screenshot({ path: path.join(out, 'failure.png') });
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    await context.tracing.stop({ path: path.join(out, 'trace.zip') });
    await browser.close();
  }
  console.log('PASS: real API and production UI confirmation/undo');
})().catch(e => { console.error(e); process.exitCode = 1; });
