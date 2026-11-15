// Incremental real-browser security/statistics checks. Owns a new isolated server;
// never connects to a user's installed instance or uses desktop automation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawn, execFileSync } = require('node:child_process');
const { once } = require('node:events');
const { createHash, randomUUID } = require('node:crypto');
const { chromium } = require('playwright');
const onlyRange = process.argv.includes('--only-statistic-range');
const onlyNavigation = process.argv.includes('--navigation-only');
const onlyPageFailure = process.argv.includes('--page-failure');

const project = path.resolve(__dirname, '..');
const run = `${new Date().toISOString().replaceAll(':', '-')}-${randomUUID().slice(0, 8)}`;
const output = path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-security', run);
const data = path.join(project, '.local/tmp/2026-09-30-deep-security', run);
fs.mkdirSync(output, { recursive: true });
fs.mkdirSync(data, { recursive: true });
const web = path.join(data, 'web-dist');
fs.cpSync(path.join(project, 'apps/web/dist'), web, { recursive: true, errorOnExist: true });
const report = { run, status: 'running', browser: 'headless Chromium', realModelCalls: 0,
  reused: ['test_local_http_security.py HTTP-client coverage', 'check-dashboard-interactions.cjs card-limit/delete-cancel', 'StatisticDialog.test.tsx stale-preview races'],
  scope: 'New actual browser foreign/null origin multipart writes, protected reads, stored markup, all-date preview, failed save retry, stale edit conflict',
  sourceHead: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: project, encoding: 'utf8', windowsHide: true }).trim(),
  fingerprint: {}, checks: [], errors: [] };
if (onlyRange) report.scope = 'Targeted replay of STAT-01 after range-label repair; other scenarios reused from baseline run';
if (onlyNavigation) report.scope = 'Necessary cold/warm navigation regression and resource measurement after lazy page loading';
if (onlyPageFailure) report.scope = 'Controlled lazy-page network failure; navigation survives and explicit reload recovers';
for (const file of ['apps/web/dist/index.html', 'apps/web/src/dashboard/StatisticDialog.tsx', 'apps/api/src/document_pipeline_api/local_http_security.py']) {
  report.fingerprint[file] = createHash('sha256').update(fs.readFileSync(path.join(project, file))).digest('hex');
}
function record(id, steps, facts) { report.checks.push({ id, status: 'passed', steps, ...facts }); }
function save() { fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify(report, null, 2)); }
async function listen(server) { server.listen(0, '127.0.0.1'); await once(server, 'listening'); return server.address().port; }
const python = path.join(project, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const env = { ...process.env, PYTHONUTF8: '1', ZHIYI_OFFLINE: '1', DOCUMENT_PIPELINE_DATA_DIR: data,
  DOCUMENT_PIPELINE_QUEUE_DB: path.join(data, 'queue.db'), DOCUMENT_PIPELINE_WORKER_HEARTBEAT: path.join(data, 'worker-heartbeat.json') };

(async () => {
  const reservation = http.createServer(); const port = await listen(reservation);
  await new Promise(resolve => reservation.close(resolve));
  const base = `http://127.0.0.1:${port}`;
  report.apiOrigin = base; report.data = path.relative(project, data).replaceAll('\\', '/');
  const log = fs.createWriteStream(path.join(output, 'server.log'));
  const server = spawn(python, ['scripts/serve-verification.py', '--data-dir', data, '--port', String(port), '--web-dir', web], { cwd: project, env, windowsHide: true });
  server.stdout.pipe(log); server.stderr.pipe(log);
  let browser, foreign, page;
  try {
    for (let n = 0; ; n++) {
      try { if ((await fetch(`${base}/api/v1/health`, { signal: AbortSignal.timeout(1000) })).ok) break; } catch {}
      assert(n < 80 && server.exitCode === null, 'isolated server failed to start');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 850 }, reducedMotion: 'reduce' });
    await context.tracing.start({ screenshots: true, snapshots: true });
    page = await context.newPage();
    page.on('pageerror', error => report.errors.push(String(error)));
    async function api(endpoint, method = 'GET', body) {
      const response = await context.request.fetch(`${base}/api/v1${endpoint}`, { method, ...(body === undefined ? {} : { data: body }) });
      assert(response.ok(), `${method} ${endpoint}: ${response.status()} ${await response.text()}`);
      return response.status() === 204 ? null : response.json();
    }
    await api('/user-state/onboarding', 'PUT', { completed_version: 999 });
    if (onlyPageFailure) {
      await page.route('**/GuidePage-*.js', route => route.abort('failed'));
      await page.goto(base);
      await page.getByRole('button', { name: '使用说明', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: '页面暂时无法打开' }).waitFor();
      await page.screenshot({ path: path.join(output, 'module-failure-recoverable.png') });
      await page.getByRole('button', { name: '状态监控', exact: true }).click();
      await page.getByRole('heading', { name: '状态监控', exact: true }).waitFor();
      await page.unroute('**/GuidePage-*.js');
      await page.getByRole('button', { name: '使用说明', exact: true }).click();
      await page.getByRole('button', { name: '重新载入知意', exact: true }).click();
      await page.getByRole('heading', { name: '状态监控', exact: true }).waitFor();
      await page.getByRole('button', { name: '使用说明', exact: true }).click();
      await page.getByRole('heading', { name: '使用说明', exact: true }).waitFor();
      record('PERF-02', ['Abort one page module request', 'Show recoverable alert', 'Other navigation still works', 'Remove fault, reload button, navigate again'], { faultInjection: 'GuidePage module fetch only', recovered: true });
      report.status = 'passed'; await context.tracing.stop({ path: path.join(output, 'trace.zip') }); save();
      console.log(JSON.stringify({ status: report.status, checks: report.checks.length, output })); return;
    }
    if (onlyNavigation) {
      await page.goto(base);
      await page.getByRole('heading', { name: '状态监控', exact: true }).waitFor();
      const initialResources = await page.evaluate(() => performance.getEntriesByType('resource').filter(entry => /\.js(?:\?|$)/.test(entry.name)).map(entry => ({ url: new URL(entry.name).pathname, bytes: entry.decodedBodySize })));
      assert(!initialResources.some(item => /(?:ExtractPage|DataTablePage|TemplatesPage|HistoryPage|ConnectionsPage|SettingsPage|BackupPage|GuidePage)-/.test(item.url)), 'secondary pages must not load at startup');
      const links = [['文件提取', '文件提取'], ['数据仓库', null], ['文件历史', '文件历史'], ['模板', '模板管理'], ['仪表盘', '数据仪表盘'], ['备份', '备份与恢复'], ['接口', '接口与 MCP'], ['使用说明', '使用说明'], ['设置', '系统设置'], ['问知意', '问知意'], ['状态监控', '状态监控']];
      const navigations = [];
      for (const pass of ['cold', 'warm']) for (const [label, heading] of links) {
        const start = performance.now();
        await page.locator('.app-sidebar').getByRole('button', { name: label, exact: true }).click();
        if (heading) await page.getByRole('heading', { name: heading, exact: true }).first().waitFor();
        else await page.locator('.sheet-tree-title').waitFor();
        navigations.push({ pass, page: label, clickToVisibleMs: Math.round(performance.now() - start) });
      }
      assert.equal(report.errors.length, 0, `page errors: ${report.errors.join(', ')}`);
      record('PERF-01', ['Cold startup resource inventory', 'No secondary page bundle requested', '11 real navigation controls cold+warm'], { initialResources, initialJsBytes: initialResources.reduce((sum, item) => sum + item.bytes, 0), navigations });
      await page.screenshot({ path: path.join(output, 'navigation-final.png') });
      report.status = 'passed'; await context.tracing.stop({ path: path.join(output, 'trace.zip') }); save();
      console.log(JSON.stringify({ status: report.status, checks: report.checks.length, output })); return;
    }
    const template = await api('/templates', 'POST', { name: '边界统计合成模板', fields: [
      { key: 'date', label: '日期', value_type: 'date', section: 'item' },
      { key: 'amount', label: '金额', value_type: 'number', section: 'item' },
      { key: 'note', label: '说明', value_type: 'text', section: 'item' },
    ] });
    const table = await api('/tables', 'POST', { name: '合成安全统计', template_key: template.id });
    const payload = '<img src=x onerror="window.__audit_executed=true">';
    for (const [date, amount, note] of [['2020-01-01', 17, payload], ['2020-02-01', 25, '合成安全材料']]) {
      await api(`/tables/${table.id}/rows`, 'POST', { values: { date, amount, note } });
    }
    const before = await api('/tables');
    const snapshot = await api(`/tables/${table.id}`);
    if (!onlyRange) {
    foreign = http.createServer((_, response) => { response.writeHead(200, { 'Content-Type': 'text/html' }); response.end('<!doctype html><title>Isolated foreign origin</title>'); });
    const foreignPort = await listen(foreign);
    const attacker = await context.newPage();
    await attacker.goto(`http://127.0.0.1:${foreignPort}`);
    const writeResponse = attacker.waitForResponse(r => r.url() === `${base}/api/v1/tables/import-new` && r.request().method() === 'POST');
    const foreignResult = await attacker.evaluate(async base => {
      const body = new FormData(); body.append('file', new File(['label\nforeign-write\n'], 'foreign.csv', { type: 'text/csv' }));
      try { const r = await fetch(`${base}/api/v1/tables/import-new`, { method: 'POST', body, mode: 'no-cors' }); return r.type; }
      catch (error) { return error.name; }
    }, base);
    assert.equal((await writeResponse).status(), 403);
    assert.deepEqual(await api('/tables'), before);
    record('SEC-01', ['Different local origin', 'Browser no-cors multipart upload', 'Verify 403 and unchanged tables'], { result: foreignResult, persistedUnchanged: true });
    const blockedRead = await attacker.evaluate(async base => { try { await fetch(`${base}/api/v1/tables`); return false; } catch { return true; } }, base);
    assert(blockedRead, 'cross-origin private data must not be browser-readable');
    record('SEC-02', ['Foreign page fetches table data', 'Browser CORS denies readable response'], { blockedRead });
    const nullResponse = attacker.waitForResponse(r => r.url() === `${base}/api/v1/tables/import-new` && r.request().method() === 'POST');
    await attacker.evaluate(base => {
      const frame = document.createElement('iframe'); frame.setAttribute('sandbox', 'allow-scripts');
      frame.srcdoc = `<script>const body=new FormData();body.append('file',new File(['label\\nnull-origin\\n'],'null.csv',{type:'text/csv'}));fetch(${JSON.stringify(base + '/api/v1/tables/import-new')},{method:'POST',body,mode:'no-cors'}).catch(()=>{});<\/script>`;
      document.body.append(frame);
    }, base);
    const nullWrite = await nullResponse;
    assert.equal(nullWrite.status(), 403);
    report.opaqueOriginResponse = { status: nullWrite.status(), body: await nullWrite.text().catch(() => 'browser-opaque') };
    assert.deepEqual(await api('/tables'), before);
    record('SEC-03', ['Sandboxed opaque iframe', 'Browser Origin:null multipart upload', 'Verify 403 and unchanged persisted table'], { persistedUnchanged: true });
    await attacker.close();
    await page.goto(base);
    await page.getByRole('button', { name: '数据仓库', exact: true }).click();
    await page.locator('.sheet-tree-item').filter({ hasText: '合成安全统计' }).click();
    await page.getByText(payload, { exact: true }).first().waitFor();
    assert.equal(await page.evaluate(() => window.__audit_executed), undefined);
    assert.equal(await page.locator('img[onerror]').count(), 0);
    await page.screenshot({ path: path.join(output, 'stored-markup-text.png') });
    assert.deepEqual(await api(`/tables/${table.id}`), snapshot);
    record('SEC-04', ['Persist synthetic HTML payload as cell data', 'Open actual warehouse UI', 'Verify literal text/no injected element or script and unchanged data'], { literalText: true });
    } else await page.goto(base);

    await page.getByRole('button', { name: '仪表盘', exact: true }).click();
    await page.getByRole('combobox', { name: '时间范围', exact: true }).selectOption('all');
    await page.getByRole('button', { name: '添加统计', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '添加统计', exact: true });
    await dialog.getByRole('combobox', { name: '数据表', exact: true }).selectOption(table.id);
    await dialog.getByRole('combobox', { name: '统计什么', exact: true }).selectOption('sum');
    await dialog.getByRole('combobox', { name: /^日期范围/ }).selectOption('date');
    await dialog.getByRole('textbox', { name: '名称', exact: true }).fill('全部日期金额');
    await dialog.getByRole('button', { name: '保存统计', exact: true }).waitFor();
    await page.waitForFunction(() => !document.querySelector('.statistic-dialog button[type=submit]').disabled);
    const previewText = await dialog.getByRole('region', { name: '统计预览' }).textContent();
    const allRangeCorrect = !previewText.includes('all') && previewText.includes('全部');
    if (!allRangeCorrect) {
      report.checks.push({ id: 'STAT-01', status: 'failed', steps: ['Dashboard 全部', 'Create statistic', 'Date range follows dashboard'], expected: '全部日期/全部数据', actual: previewText });
      await page.screenshot({ path: path.join(output, 'all-range-label-failed.png') });
    } else record('STAT-01', ['Dashboard 全部', 'Create statistic', 'Date range follows dashboard'], { previewText });
    if (onlyRange) {
      assert(allRangeCorrect, 'all-date preview label must be human readable');
      await page.screenshot({ path: path.join(output, 'all-range-label-fixed.png') });
      report.status = 'passed';
      await context.tracing.stop({ path: path.join(output, 'trace.zip') });
      save(); console.log(JSON.stringify({ status: report.status, checks: report.checks.length, output })); return;
    }
    // New transport failure sequence: persist no card on a failed save, retain draft,
    // then retry through the actual API exactly once; source data is independent.
    let intercepted = 0;
    await page.route(`${base}/api/v1/stats/cards?days=all`, async route => {
      if (route.request().method() === 'POST' && intercepted++ === 0) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '合成临时故障，可重试' }) });
      return route.continue();
    });
    await dialog.getByRole('button', { name: '保存统计', exact: true }).click();
    await dialog.getByRole('alert').filter({ hasText: '合成临时故障' }).waitFor();
    assert.equal((await api('/stats/cards')).items.length, 0);
    assert.equal(await dialog.getByRole('textbox', { name: '名称', exact: true }).inputValue(), '全部日期金额');
    await dialog.getByRole('button', { name: '保存统计', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    let cards = (await api('/stats/cards')).items;
    assert.equal(cards.length, 1);
    const result = await api(`/stats/cards/${cards[0].id}/result?days=all`);
    const metric = result.analysis.metric_keys[0];
    assert.equal(result.analysis.data[0][metric], 42, 'independent golden: 17 + 25 = 42');
    assert.deepEqual(await api(`/tables/${table.id}`), snapshot);
    record('STAT-02', ['503 injected on first save', 'Draft preserved/no persisted card', 'Retry real HTTP', 'Exactly one card, independent total42, unchanged source'], { faultInjection: 'first save 503 only', attempts: intercepted, sum: 42 });
    await page.getByRole('button', { name: '管理统计：全部日期金额', exact: true }).click();
    await page.getByRole('menuitem', { name: '修改统计', exact: true }).click();
    const editor = page.getByRole('dialog', { name: '修改统计', exact: true });
    await editor.getByRole('textbox', { name: '名称', exact: true }).fill('不应覆盖并发改动');
    cards = (await api('/stats/cards')).items;
    const { id, position, created_at, updated_at, ...definition } = cards[0];
    await api(`/stats/cards/${id}?days=all`, 'PUT', { ...definition, name: '另一窗口已更新', expected_updated_at: updated_at });
    await page.waitForFunction(() => !document.querySelector('.statistic-dialog button[type=submit]').disabled);
    await editor.getByRole('button', { name: '保存统计', exact: true }).click();
    await editor.getByRole('alert').waitFor();
    assert.equal((await api('/stats/cards')).items[0].name, '另一窗口已更新');
    assert.equal(await editor.getByRole('textbox', { name: '名称', exact: true }).inputValue(), '不应覆盖并发改动');
    await page.screenshot({ path: path.join(output, 'concurrent-statistic-protected.png') });
    await editor.getByRole('button', { name: '取消', exact: true }).click();
    record('STAT-03', ['Edit card in browser', 'Concurrent real HTTP update', 'Old draft save rejected', 'New persistent state and local draft both preserved', 'Cancel'], { concurrentProtected: true });
    // Small local latency sample, not a scale certification.
    const elapsed = [];
    for (let index = 0; index < 5; index++) { const start = performance.now(); await api(`/stats/cards/${cards[0].id}/result?days=all`); elapsed.push(Math.round(performance.now() - start)); }
    report.performance = { sample: '2 synthetic rows, loopback API', resultLatencyMs: elapsed, noScaleClaim: true };
    assert.equal(report.errors.length, 0, `browser page errors: ${report.errors.join(', ')}`);
    report.status = report.checks.some(check => check.status === 'failed') ? 'failed' : 'passed';
    await context.tracing.stop({ path: path.join(output, 'trace.zip') });
    save();
    console.log(JSON.stringify({ status: report.status, checks: report.checks.length, output }));
    if (report.status !== 'passed') process.exitCode = 1;
  } catch (error) {
    if (page) { await page.screenshot({ path: path.join(output, 'failed.png') }).catch(() => {}); fs.writeFileSync(path.join(output, 'failed-dom.txt'), await page.locator('body').innerText().catch(() => 'unavailable')); }
    report.status = 'failed'; report.failure = String(error); save(); throw error;
  } finally {
    if (browser) await browser.close();
    if (foreign) await new Promise(resolve => foreign.close(resolve));
    if (server.exitCode === null) { server.kill(); await once(server, 'exit'); }
    log.end();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
