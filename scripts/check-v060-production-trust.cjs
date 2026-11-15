// Real headless browser -> isolated HTTP API -> two loopback provider substitutes.
// Credentials are synthetic and memory-only. No traces, screenshots, cloud or OS store.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const crypto = require('node:crypto');
const { once } = require('node:events');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const id = `${new Date().toISOString().replace(/[:.]/g, '-')}-${crypto.randomBytes(4).toString('hex')}`;
const evidence = path.join(root, '.local/evidence/v0.6.0/2026-09-30-production-trust', id);
const runtime = path.join(root, '.local/tmp/2026-09-30-production-trust', id);
const web = path.join(runtime, 'web');
const data = path.join(runtime, 'data');
const keyA = 'synthetic-browser-service-a-disposable';
const keyB = 'synthetic-browser-service-b-disposable';
const report = { id, status: 'running', case: 'TRUST-BROWSER-01',
  reason: 'Changed credential binding; new service-switch/probe/save/reload sequence',
  real_browser: 'headless Chromium', real_api: true, model_boundary: 'two loopback HTTP substitutes',
  real_cloud_calls: 0, credential_store: 'ephemeral memory', checks: [] };
let api, browser, log, providerA, providerB;

function check(value, message) { if (!value) throw new Error(message); }
function record(step) { report.checks.push(step); }
function hash(file) { return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'); }
function files(dir) { return fs.readdirSync(dir, { withFileTypes: true }).flatMap(item => {
  const full = path.join(dir, item.name); return item.isDirectory() ? files(full) : [full];
}); }
async function provider() {
  const calls = [];
  const server = http.createServer((request, response) => {
    request.resume();
    calls.push({ method: request.method, path: request.url, has_auth: !!request.headers.authorization,
      matches_a: request.headers.authorization === `Bearer ${keyA}`,
      matches_b: request.headers.authorization === `Bearer ${keyB}` });
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(request.method === 'GET'
      ? { data: [{ id: 'synthetic-model' }] }
      : { choices: [{ message: { content: '{"ok":true}' } }] }));
  });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  return { server, calls, url: `http://127.0.0.1:${server.address().port}/v1` };
}
async function waitFor(fn) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (api?.exitCode !== null && api?.exitCode !== undefined) throw new Error('Owned API exited');
    try { if (await fn()) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error('Timed out waiting for isolated state');
}

(async () => {
  fs.mkdirSync(evidence, { recursive: true }); fs.mkdirSync(runtime, { recursive: true });
  try {
    fs.cpSync(path.join(root, 'apps/web/dist'), web, { recursive: true, errorOnExist: true, force: false });
    report.assets = Object.fromEntries(files(web).map(file => [path.relative(web, file), hash(file)]));
    report.source = Object.fromEntries(['apps/api/src/document_pipeline_api/services/model_profiles.py',
      'apps/web/src/components/ModelProfilesSettings.tsx', 'scripts/serve-verification.py',
      'scripts/check-v060-production-trust.cjs'].map(file => [file, hash(path.join(root, file))]));
    providerA = await provider(); providerB = await provider();
    const socket = net.createServer(); socket.listen(0, '127.0.0.1'); await once(socket, 'listening');
    const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
    const origin = `http://127.0.0.1:${port}`;
    const env = { ...process.env };
    for (const name of Object.keys(env)) {
      if (/API.?KEY|TOKEN|SECRET|PASSWORD|OPENAI|DASHSCOPE|AZURE|MODEL_|MCP_/i.test(name)) delete env[name];
    }
    Object.assign(env, { PYTHONUTF8: '1', DOCUMENT_PIPELINE_DATA_DIR: data,
      DOCUMENT_PIPELINE_QUEUE_DB: path.join(data, 'queue.db') });
    log = fs.createWriteStream(path.join(evidence, 'api.log'));
    api = spawn(path.join(root, 'apps/api/.venv/Scripts/python.exe'), ['scripts/serve-verification.py',
      '--data-dir', data, '--web-dir', web, '--port', String(port), '--memory-credentials'],
    { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    api.stdout.pipe(log); api.stderr.pipe(log);
    await waitFor(async () => (await fetch(`${origin}/api/v1/health`, { signal: AbortSignal.timeout(500) })).ok);
    async function json(route, method = 'GET', body) {
      const response = await fetch(`${origin}/api/v1${route}`, { method,
        headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      check(response.ok, `API status ${response.status}`); return response.json();
    }
    const profile = await json('/models/profiles', 'POST', { name: '凭据绑定合成验收',
      provider: 'openai_compatible', base_url: providerA.url, model_name: 'synthetic-model', api_key: keyA });
    await json(`/models/profiles/${profile.id}/activate`, 'POST', { version: 1 });
    const current = async () => (await json('/models/profiles')).find(item => item.id === profile.id);
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1365, height: 950 } });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await context.addInitScript(() => localStorage.setItem('onboarding-done-v4', '1'));
    const page = await context.newPage(); await page.goto(origin);
    await page.locator('.app-sidebar').getByRole('button', { name: '设置', exact: true }).click();
    await page.locator('.profile-list').getByRole('button', { name: /凭据绑定合成验收/ }).click();
    const field = page.locator('#model-profile-key');
    check((await field.getAttribute('placeholder')).includes('更换服务'), 'Missing service-change guidance');
    async function probe() {
      const response = page.waitForResponse(r => r.url().includes('/models/profiles/probe') && r.request().method() === 'POST');
      await page.getByRole('button', { name: '测试连接', exact: true }).click();
      check((await response).ok(), 'Probe endpoint failed');
      await page.getByRole('button', { name: '测试连接', exact: true }).waitFor();
    }
    await probe(); check(providerA.calls.some(call => call.matches_a), 'Same service did not receive its credential');
    record('same-service probe retains original synthetic credential');
    await page.locator('#model-profile-url').fill(providerB.url);
    await probe(); check(providerB.calls.length > 0 && providerB.calls.every(call => !call.has_auth), 'Changed service received inherited credential');
    record('changed-service probe carries no inherited credential');
    await page.getByRole('button', { name: '保存修改', exact: true }).click();
    await waitFor(async () => (await current()).version === 2);
    await page.getByText('已保存为新版本；排队中的旧任务不会改变。', { exact: true }).waitFor();
    check(!(await current()).has_api_key, 'Changed service save retained old credential');
    await page.reload();
    await page.locator('.app-sidebar').getByRole('button', { name: '设置', exact: true }).click();
    await page.locator('.profile-list').getByRole('button', { name: /凭据绑定合成验收/ }).click();
    check(await field.inputValue() === '', 'Reload exposed a saved credential');
    check(await page.locator('#model-profile-url').inputValue() === providerB.url, 'Reload lost saved service');
    record('save and reload persist new service without inherited or visible credential');
    await field.fill(keyB);
    await page.getByRole('button', { name: '保存修改', exact: true }).click();
    await waitFor(async () => (await current()).version === 3);
    await page.getByText('已保存为新版本；排队中的旧任务不会改变。', { exact: true }).waitFor();
    check(await field.inputValue() === '', 'Save left credential visible in input');
    const offset = providerB.calls.length; await probe();
    check(providerB.calls.slice(offset).some(call => call.matches_b), 'Explicit new credential was not used');
    check(providerB.calls.every(call => !call.matches_a), 'Old service credential reached new service');
    record('explicit new credential works after save; old credential never reaches changed service');
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed'; report.failure = { type: error.name, message: String(error.message).split('\n')[0].slice(0, 180) };
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (api && api.exitCode === null) { api.kill(); await once(api, 'exit'); }
    if (log) await new Promise(resolve => log.end(resolve));
    for (const item of [providerA, providerB]) if (item) {
      item.server.closeAllConnections(); await new Promise(resolve => item.server.close(resolve));
    }
    report.cleanup = 'owned browser/API/provider servers closed; memory-only credentials discarded';
    fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ status: report.status, checks: report.checks.length, evidence: path.relative(root, evidence) }));
  }
})();
