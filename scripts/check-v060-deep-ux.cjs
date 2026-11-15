// Headless incremental regression for newly reproduced v0.6.0 draft-loss gaps.
// It owns an isolated API database and freezes the already-built frontend. Injected
// UI requests are synthetic model probes/generation plus the first permission-save
// failure. No screenshot, trace, HAR, token, or response body is written to evidence.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const { once } = require('node:events');
const { chromium } = require('playwright');

const project = path.resolve(__dirname, '..');
const stamp = new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-');
const runId = `${stamp}-deep-ux-${crypto.randomBytes(4).toString('hex')}`;
const evidenceRoot = path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-ux', runId);
const runtimeRoot = path.join(project, '.local/tmp/2026-09-30-deep-ux', runId);
const dataDir = path.join(runtimeRoot, 'data');
const frozenWeb = path.join(runtimeRoot, 'web-dist');
const evidenceFile = path.join(evidenceRoot, 'report.json');
const python = process.env.ZHIYI_TEST_PYTHON || path.join(project, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const allCaseIds = [
  'MODEL-DRAFT-GUARD',
  'MODEL-PROBE-INVALIDATION',
  'MCP-PERM-DRAFT-GUARD',
  'MCP-PERM-SAVE-RETRY',
  'MCP-KEY-REFRESH-DRAFT-ISOLATION',
  'MCP-INITIAL-BASELINE-GUARD',
  'TEMPLATE-GENERATE-STOP-WAITING',
];
const onlyArgument = process.argv.find((argument) => argument.startsWith('--only='));
const selectedCaseIds = onlyArgument ? new Set(onlyArgument.slice('--only='.length).split(',').map((id) => id.trim()).filter(Boolean)) : null;
const report = {
  run_id: runId,
  scope: 'incremental model-profile, MCP-permission baseline, and template-generation interaction fixes',
  browser: 'Playwright headless Chromium; no native desktop or visible window',
  data_boundary: 'isolated SQLite and storage under .local/tmp; no user database or files',
  evidence_boundary: 'no screenshot, trace, HAR, DOM dump, credential, or response body',
  real_api: true,
  real_model_calls: 0,
  injected_requests: { model_probe: 0, first_permission_save_failure: 0, initial_settings_failure: 0, synthetic_template_generations: 0 },
  real_permission_save_retries: 0,
  real_settings_retry_gets: 0,
  external_browser_requests: 0,
  checks: [],
  selected_cases: selectedCaseIds ? [...selectedCaseIds] : allCaseIds,
  skipped_cases: [],
};
let server;
let browser;
let context;
let serverLog;

function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function walkFiles(root, current = root) {
  return fs.readdirSync(current, { withFileTypes: true }).flatMap((entry) => {
    const absolute = path.join(current, entry.name);
    return entry.isDirectory() ? walkFiles(root, absolute) : [path.relative(root, absolute).replaceAll('\\', '/')];
  });
}

function shaTree(root) {
  return Object.fromEntries(walkFiles(root).sort().map((relative) => [relative, sha256(path.join(root, relative))]));
}

function writeReport() {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  fs.writeFileSync(evidenceFile, JSON.stringify(report, null, 2), 'utf8');
  writeScenarioMatrix(evidenceRoot, report);
}

function writeScenarioMatrix(root, source) {
  const matrix = {
    schema_version: 1,
    run_id: source.run_id,
    browser: source.browser,
    real_api: source.real_api,
    real_model_calls: source.real_model_calls,
    injected_requests: source.injected_requests,
    skipped_cases: source.skipped_cases ?? [],
    cases: (source.checks ?? []).map((check) => ({
      id: check.id,
      status: check.status,
      entry_state: check.steps?.[0] ?? null,
      sequence: check.steps ?? [],
      assertions: check.assertions ?? null,
      boundary: check.request_boundary ?? null,
      failure: check.failure ?? null,
    })),
  };
  fs.writeFileSync(path.join(root, 'scenario-matrix.json'), JSON.stringify(matrix, null, 2), 'utf8');
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function getJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(8000) });
  const value = await response.json();
  assert(response.ok, `real API request failed (${response.status})`);
  return value;
}

async function waitFor(predicate, description, attempts = 40) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      const value = await predicate();
      if (value) return value;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`timed out waiting for ${description}`);
}

async function runCheck(id, steps, fn) {
  if (selectedCaseIds && !selectedCaseIds.has(id)) {
    report.skipped_cases.push(id);
    writeReport();
    return;
  }
  const entry = { id, status: 'running', steps };
  report.checks.push(entry);
  let page;
  try {
    page = await context.newPage();
    page.on('request', (request) => {
      try {
        if (new URL(request.url()).origin !== baseOrigin) report.external_browser_requests += 1;
      } catch {}
    });
    await page.addInitScript(() => localStorage.setItem('onboarding-done-v4', '1'));
    await page.goto(baseOrigin, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.getByRole('button', { name: '设置', exact: true }).waitFor({ state: 'visible', timeout: 20000 });
    await fn(page, entry);
    entry.status = 'passed';
  } catch (error) {
    entry.status = 'failed';
    // Keep browser/DOM content out of the report. The scoped failure message is safe.
    entry.failure = {
      name: error && typeof error.name === 'string' ? error.name : 'Error',
      reason: error && typeof error.message === 'string' ? error.message.slice(0, 180) : 'Browser assertion failed',
    };
  } finally {
    if (page) await page.close().catch(() => undefined);
    writeReport();
  }
}

async function clickNav(page, label) {
  await page.locator('.app-sidebar').getByRole('button', { name: label, exact: true }).click();
}

async function openSettings(page) {
  await clickNav(page, '设置');
  await page.getByRole('heading', { name: '系统设置' }).waitFor({ state: 'visible' });
  await page.locator('#model-profile-model').waitFor({ state: 'visible' });
}

async function permissionsFromRealApi() {
  return getJson(`${baseOrigin}/api/v1/integration/settings`);
}

async function setup() {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  fs.mkdirSync(runtimeRoot, { recursive: true });
  const liveDist = path.join(project, 'apps/web/dist');
  assert(fs.existsSync(path.join(liveDist, 'index.html')), 'frontend build is missing');
  fs.cpSync(liveDist, frozenWeb, { recursive: true, errorOnExist: true, force: false });

  const sourcePaths = [
    'apps/web/src/App.tsx',
    'apps/web/src/api.ts',
    'apps/web/src/components/TemplatesPage.tsx',
    'apps/web/src/components/TemplatesPage.test.tsx',
    'apps/web/src/components/ModelProfilesSettings.tsx',
    'apps/web/src/components/ConnectionsPage.tsx',
    'apps/web/src/components/ConnectionsPage.test.tsx',
    'apps/web/src/useUnsavedChanges.tsx',
  ];
  let head = 'unavailable';
  try { head = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: project, encoding: 'utf8' }).trim(); } catch {}
  let buildManifest = null;
  const manifestPath = path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-governance/build-manifest-baseline-final.json');
  if (fs.existsSync(manifestPath)) {
    try { buildManifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8')); } catch {}
  }
  report.source = { head, files: Object.fromEntries(sourcePaths.map((relative) => [relative, sha256(path.join(project, relative))])) };
  report.build = {
    origin: 'apps/web/dist frozen immediately after parent final build',
    asset_count: walkFiles(frozenWeb).length,
    assets_sha256: shaTree(frozenWeb),
    parent_build_manifest: buildManifestPathExists() ? manifestPath : null,
    parent_build_manifest_sha256: buildManifest ? sha256(manifestPath) : null,
  };
  report.runtime_dir = path.relative(project, runtimeRoot).replaceAll('\\', '/');
  report.evidence_dir = path.relative(project, evidenceRoot).replaceAll('\\', '/');
  writeReport();

  const socket = net.createServer();
  socket.listen(0, '127.0.0.1');
  await once(socket, 'listening');
  const port = socket.address().port;
  await new Promise((resolve) => socket.close(resolve));
  baseOrigin = `http://127.0.0.1:${port}`;
  report.base_origin = baseOrigin;

  const env = { ...process.env, PYTHONUTF8: '1', ZHIYI_OFFLINE: '1' };
  // Do not pass ambient provider credentials or model settings into this synthetic API.
  for (const key of Object.keys(env)) {
    if (/API.?KEY|TOKEN|SECRET|PASSWORD|OPENAI|DASHSCOPE|AZURE|MODEL_PROVIDER|MODEL_NAME|MODEL_BASE_URL|MCP_/i.test(key)) delete env[key];
  }
  env.ZHIYI_OFFLINE = '1';
  env.DOCUMENT_PIPELINE_DATA_DIR = dataDir;
  env.DOCUMENT_PIPELINE_QUEUE_DB = path.join(dataDir, 'queue.db');
  env.DOCUMENT_PIPELINE_WORKER_HEARTBEAT = path.join(dataDir, 'worker-heartbeat.json');
  env.ZHIYI_ACCEPTANCE_URL = baseOrigin;
  env.ZHIYI_ACCEPTANCE_API = `${baseOrigin}/api/v1`;
  delete env.ZHIYI_FAST;

  serverLog = fs.createWriteStream(path.join(runtimeRoot, 'server.log'));
  server = spawn(python, [
    'scripts/serve-verification.py', '--data-dir', dataDir, '--web-dir', frozenWeb, '--port', String(port),
  ], { cwd: project, env, windowsHide: true });
  server.stdout.pipe(serverLog);
  server.stderr.pipe(serverLog);
  server.on('error', () => { report.server_spawn_error = true; });
  await waitFor(async () => {
    if (server.exitCode !== null) throw new Error('verification API exited during startup');
    const response = await fetch(`${baseOrigin}/api/v1/templates`, { signal: AbortSignal.timeout(700) });
    return response.ok;
  }, 'isolated API');

  // Listing profiles is a real local API read and seeds only the isolated default record.
  const profiles = await getJson(`${baseOrigin}/api/v1/models/profiles`);
  if (!profiles.some((profile) => !profile.is_archived)) {
    const response = await fetch(`${baseOrigin}/api/v1/models/profiles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'v060 deep UX synthetic', provider: 'lm_studio', base_url: 'http://127.0.0.1:1234/v1', model_name: 'synthetic-model', timeout_seconds: 180 }),
    });
    assert(response.ok, `synthetic profile setup failed (${response.status})`);
  }

  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ viewport: { width: 1365, height: 900 }, acceptDownloads: false });
}

function buildManifestPathExists() {
  return fs.existsSync(path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-governance/build-manifest-baseline-final.json'));
}

let baseOrigin = '';

if (process.argv[2] === '--matrix-from' && process.argv[3]) {
  const sourcePath = path.resolve(process.argv[3]);
  const source = JSON.parse(fs.readFileSync(sourcePath, 'utf8'));
  writeScenarioMatrix(path.dirname(sourcePath), source);
  process.exit(0);
}

async function main() {
  try {
    assert(selectedCaseIds === null || selectedCaseIds.size > 0, 'use --only=CASE-ID with at least one known case');
    const unknownCases = selectedCaseIds ? [...selectedCaseIds].filter((id) => !allCaseIds.includes(id)) : [];
    assert(unknownCases.length === 0, 'unknown --only case ID');
    await setup();

    await context.route('**/api/v1/models/profiles/probe**', async (route) => {
      report.injected_requests.model_probe += 1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ connected: true, model_listed: true, multimodal: true, message: '合成探测通过（未请求模型服务）', available_models: ['synthetic-model'] }),
      });
    });

    await runCheck('MODEL-DRAFT-GUARD', [
      'open Settings > AI service configuration from the left navigation',
      'edit model name without saving; navigate to Templates; cancel the unsaved dialog and verify the edit remains',
      'navigate again; discard the model draft; return to Settings and verify the server baseline is restored',
    ], async (page, entry) => {
      await openSettings(page);
      const input = page.locator('#model-profile-model');
      const before = await input.inputValue();
      assert(before.length > 0, 'synthetic profile did not load');
      const draft = `${before}-uxdraft`;
      await input.fill(draft);
      await clickNav(page, '模板');
      let dialog = page.getByRole('dialog', { name: '修改尚未保存' });
      await dialog.waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: '取消' }).click();
      assert(await input.inputValue() === draft, 'cancel discarded model-profile input');
      await clickNav(page, '模板');
      dialog = page.getByRole('dialog', { name: '修改尚未保存' });
      await dialog.waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: '放弃修改并继续' }).click();
      await page.getByRole('heading', { name: '模板管理' }).waitFor({ state: 'visible' });
      await openSettings(page);
      assert(await input.inputValue() === before, 'discard did not restore the persisted profile');
      entry.assertions = { cancel_retains_draft: true, discard_restores_real_api_profile: true };
    });

    await runCheck('MODEL-PROBE-INVALIDATION', [
      'open the isolated model profile and change its model name',
      'intercept exactly one probe with a synthetic connected/multimodal result',
      'after the result settles, edit the model name and verify result and capability are cleared',
    ], async (page, entry) => {
      await openSettings(page);
      const input = page.locator('#model-profile-model');
      const before = await input.inputValue();
      await input.fill(`${before}-probe`);
      const probe = page.getByRole('button', { name: '测试连接', exact: true });
      await probe.click();
      const syntheticMessage = page.getByText('合成探测通过（未请求模型服务）', { exact: true });
      await syntheticMessage.waitFor({ state: 'visible' });
      const status = page.locator('.multimodal-status');
      assert(await status.getByText('支持图片', { exact: true }).isVisible(), 'synthetic probe result was not shown');
      await input.fill(`${before}-probe-edited`);
      await syntheticMessage.waitFor({ state: 'hidden' });
      assert(await status.getByText('未验证', { exact: true }).isVisible(), 'stale multimodal capability remained visible');
      entry.assertions = { synthetic_probe_result_shown: true, result_cleared_after_identity_edit: true, capability_reset_to_unverified: true };
      entry.request_boundary = { injected_probe_requests: report.injected_requests.model_probe, real_probe_or_provider_calls: 0 };
    });
    await context.unroute('**/api/v1/models/profiles/probe**');

    await runCheck('MCP-PERM-DRAFT-GUARD', [
      'open Interfaces and toggle 读取模板 without saving',
      'navigate to Settings and cancel the warning; verify draft remains',
      'navigate again and discard; return to Interfaces and verify persisted off state',
    ], async (page, entry) => {
      await clickNav(page, '接口');
      await page.getByRole('heading', { name: '接口与 MCP' }).waitFor({ state: 'visible' });
      const toggle = page.getByRole('button', { name: '读取模板', exact: true });
      await waitFor(async () => (await toggle.getAttribute('class'))?.includes('off'), 'initial persisted MCP permission');
      await toggle.click();
      assert((await toggle.getAttribute('class'))?.includes('on'), 'toggle did not change locally');
      await clickNav(page, '设置');
      let dialog = page.getByRole('dialog', { name: '修改尚未保存' });
      await dialog.waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: '取消' }).click();
      assert((await toggle.getAttribute('class'))?.includes('on'), 'cancel lost the MCP draft');
      await clickNav(page, '设置');
      dialog = page.getByRole('dialog', { name: '修改尚未保存' });
      await dialog.waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: '放弃修改并继续' }).click();
      await page.getByRole('heading', { name: '系统设置' }).waitFor({ state: 'visible' });
      await clickNav(page, '接口');
      await page.getByRole('heading', { name: '接口与 MCP' }).waitFor({ state: 'visible' });
      const restored = page.getByRole('button', { name: '读取模板', exact: true });
      await waitFor(async () => (await restored.getAttribute('class'))?.includes('off'), 'discarded permission draft reset');
      assert((await permissionsFromRealApi()).template_read === false, 'discard mutated persisted permission');
      entry.assertions = { cancel_retains_toggle: true, discard_restores_server_state: true, persisted_setting_unchanged: true };
    });

    const permissionRoute = '**/api/v1/integration/settings/permissions';
    let permissionSaveRequests = 0;
    await context.route(permissionRoute, async (route) => {
      permissionSaveRequests += 1;
      if (permissionSaveRequests === 1) {
        report.injected_requests.first_permission_save_failure += 1;
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'synthetic audit injected save failure' }) });
      } else {
        report.real_permission_save_retries += 1;
        await route.continue();
      }
    });
    await runCheck('MCP-PERM-SAVE-RETRY', [
      'toggle 读取模板 and submit permission save',
      'inject a 503 for the first browser request; verify draft remains on and API state remains off',
      'retry; let the second POST reach the isolated real API; verify persistence and clean navigation',
    ], async (page, entry) => {
      await clickNav(page, '接口');
      await page.getByRole('heading', { name: '接口与 MCP' }).waitFor({ state: 'visible' });
      const toggle = page.getByRole('button', { name: '读取模板', exact: true });
      await waitFor(async () => (await toggle.getAttribute('class'))?.includes('off'), 'initial permissions before retry scenario');
      await toggle.click();
      await page.getByRole('button', { name: '保存权限', exact: true }).click();
      await page.getByText('synthetic audit injected save failure', { exact: true }).waitFor({ state: 'visible' });
      assert((await toggle.getAttribute('class'))?.includes('on'), 'failed save discarded permission draft');
      assert((await permissionsFromRealApi()).template_read === false, 'injected failed request reached persistence');
      await page.getByRole('button', { name: '保存权限', exact: true }).click();
      await page.getByText(/MCP 权限已保存/).waitFor({ state: 'visible' });
      const saved = await waitFor(async () => {
        const value = await permissionsFromRealApi();
        return value.template_read === true ? value : null;
      }, 'real permission retry persistence');
      assert(saved.template_read === true, 'real retry did not persist');
      await page.getByRole('button', { name: '保存权限', exact: true }).waitFor({ state: 'visible' });
      await clickNav(page, '设置');
      assert(!(await page.getByRole('dialog', { name: '修改尚未保存' }).count()), 'clean permissions incorrectly blocked navigation');
      entry.assertions = { failed_save_kept_draft: true, injected_failure_did_not_mutate_api: true, real_retry_persisted: true, clean_navigation_allowed: true };
      entry.request_boundary = { injected_failures: 1, real_permission_posts: report.real_permission_save_retries };
    });
    await context.unroute(permissionRoute);

    await runCheck('MCP-KEY-REFRESH-DRAFT-ISOLATION', [
      'on Interfaces toggle 读取任务 but do not save',
      'generate a read key in the isolated config; close one-time display without reading or logging its value',
      'verify permission draft remains; revoke the synthetic key; verify it is absent from settings',
    ], async (page, entry) => {
      let generated = false;
      let revoked = false;
      try {
        await clickNav(page, '接口');
        await page.getByRole('heading', { name: '接口与 MCP' }).waitFor({ state: 'visible' });
        const toggle = page.getByRole('button', { name: '读取任务', exact: true });
        await waitFor(async () => (await toggle.getAttribute('class'))?.includes('off'), 'persisted task-read off state');
        await toggle.click();
        await page.getByRole('button', { name: '生成读密钥', exact: true }).click();
        await page.getByRole('dialog', { name: '新密钥' }).waitFor({ state: 'visible' });
        generated = true;
        assert((await toggle.getAttribute('class'))?.includes('on'), 'key-generation settings refresh overwrote permission draft');
        await page.getByRole('button', { name: '关闭', exact: true }).click();
        assert((await toggle.getAttribute('class'))?.includes('on'), 'closing one-time key view reset permission draft');
        await page.getByRole('button', { name: '撤销读', exact: true }).click();
        const confirm = page.getByRole('dialog', { name: '撤销密钥' });
        await confirm.waitFor({ state: 'visible' });
        await confirm.getByRole('button', { name: '确认撤销' }).click();
        await page.getByText('读密钥已撤销，HTTP API 立即生效。', { exact: true }).waitFor({ state: 'visible' });
        revoked = true;
        assert((await toggle.getAttribute('class'))?.includes('on'), 'key-revocation settings refresh overwrote permission draft');
        const persisted = await waitFor(async () => {
          const value = await permissionsFromRealApi();
          return value.read_token_set === false ? value : null;
        }, 'synthetic key revocation persistence');
        assert(persisted.task_read === false, 'unsaved task-read toggle was persisted by key actions');
        entry.assertions = { key_refresh_preserved_permission_draft: true, synthetic_key_revoked: true, unsaved_permission_not_persisted: true };
      } finally {
        if (generated && !revoked) {
          const cleanup = await fetch(`${baseOrigin}/api/v1/integration/settings/keys/revoke`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: 'read' }),
          }).catch(() => null);
          entry.synthetic_key_cleanup = cleanup ? cleanup.ok : false;
        }
      }
    });

    const originalIntegrationSettings = await permissionsFromRealApi();
    const baselinePermissions = {
      data_scope: 'selected', table_ids: [], task_read: true, template_read: false,
      result_read: false, data_read: true, task_control: false, write_enabled: false,
      file_access: false, file_roots: [],
    };
    const seededPermissions = await fetch(`${baseOrigin}/api/v1/integration/settings/permissions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(baselinePermissions),
    });
    assert(seededPermissions.ok, 'isolated API permission baseline setup failed');
    const seededTokenKinds = [];
    for (const kind of ['read', 'write']) {
      const seededToken = await fetch(`${baseOrigin}/api/v1/integration/settings/keys`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }),
      });
      assert(seededToken.ok, `isolated synthetic ${kind} key setup failed`);
      seededTokenKinds.push(kind);
    }

    const initialSettingsRoute = '**/api/v1/integration/settings';
    let browserSettingsGets = 0;
    let initialSettingsHeld = false;
    let releaseInitialSettingsFailure;
    let releaseSettingsRetry;
    await context.route(initialSettingsRoute, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      browserSettingsGets += 1;
      if (browserSettingsGets === 1) {
        initialSettingsHeld = true;
        report.injected_requests.initial_settings_failure += 1;
        await new Promise((resolve) => { releaseInitialSettingsFailure = resolve; });
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'synthetic initial settings read failure' }),
        }).catch(() => undefined);
      } else if (browserSettingsGets === 2) {
        report.real_settings_retry_gets += 1;
        await new Promise((resolve) => { releaseSettingsRetry = resolve; });
        await route.continue();
      } else {
        await route.continue();
      }
    });
    try {
      await runCheck('MCP-INITIAL-BASELINE-GUARD', [
        'seed the isolated real API with task_read=true and data_scope=selected plus isolated read/write keys',
        'hold the first browser settings GET; verify both key generators, both revokers, permission toggles, and Save are disabled',
        'inject a 503 for initial settings; verify all key controls and Save remain disabled and there is one re-read entry',
        'use the re-read action; verify controls match the real API baseline, all four key controls unlock, and no browser write occurred',
      ], async (page, entry) => {
        let browserPermissionPosts = 0;
        let browserKeyPosts = 0;
        page.on('request', (request) => {
          try {
            const url = new URL(request.url());
            if (url.origin !== baseOrigin || request.method() !== 'POST') return;
            if (url.pathname.endsWith('/integration/settings/permissions')) browserPermissionPosts += 1;
            if (url.pathname.endsWith('/integration/settings/keys') || url.pathname.endsWith('/integration/settings/keys/revoke')) browserKeyPosts += 1;
          } catch {}
        });
        const keyLabels = ['生成读密钥', '生成写密钥', '撤销读', '撤销写'];
        await clickNav(page, '接口');
        await page.getByRole('heading', { name: '接口与 MCP' }).waitFor({ state: 'visible' });
        await waitFor(() => initialSettingsHeld, 'held initial settings GET');
        for (const label of keyLabels) assert(await page.getByRole('button', { name: label, exact: true }).isDisabled(), `${label} was enabled before the permission baseline loaded`);
        assert(await page.getByRole('button', { name: '保存权限', exact: true }).isDisabled(), 'permission save was enabled before settings loaded');
        assert(await page.getByRole('button', { name: '读取任务', exact: true }).isDisabled(), 'permission toggle was enabled before settings loaded');

        releaseInitialSettingsFailure();
        await page.getByRole('button', { name: '重新读取', exact: true }).waitFor({ state: 'visible' });
        assert(await page.getByRole('button', { name: '重新读取', exact: true }).count() === 1, 'settings error exposed multiple re-read entry points');
        for (const label of keyLabels) assert(await page.getByRole('button', { name: label, exact: true }).isDisabled(), `${label} was enabled after initial settings failed`);
        assert(await page.getByRole('button', { name: '保存权限', exact: true }).isDisabled(), 'permission save was enabled after settings failed');

        await page.getByRole('button', { name: '重新读取', exact: true }).click();
        await waitFor(() => browserSettingsGets === 2 && report.real_settings_retry_gets === 1, 'single real settings retry GET');
        const retrying = page.getByRole('button', { name: '正在读取…', exact: true });
        await retrying.waitFor({ state: 'visible' });
        assert(await retrying.isDisabled(), 're-read action remained active while its request was pending');
        await retrying.evaluate((button) => button.click()); // Native disabled-button activation must not fire.
        await page.waitForTimeout(100);
        assert(browserSettingsGets === 2, 'a repeated click started another settings GET');
        releaseSettingsRetry();
        await waitFor(async () => (await page.getByRole('button', { name: '读取任务', exact: true }).getAttribute('class'))?.includes('on'), 'server task-read permission applied');
        assert((await page.getByRole('button', { name: '读取数据仓库', exact: true }).getAttribute('class'))?.includes('on'), 'data-read permission did not match the server baseline');
        assert((await page.getByRole('button', { name: '读取模板', exact: true }).getAttribute('class'))?.includes('off'), 'default permission draft replaced the server template-read baseline');
        assert(await page.getByLabel('允许访问的数据表').inputValue() === 'selected', 'data scope did not match the selected server baseline');
        for (const label of keyLabels) assert(await page.getByRole('button', { name: label, exact: true }).isEnabled(), `${label} did not unlock after a successful baseline read`);
        assert(await page.getByRole('button', { name: '保存权限', exact: true }).isEnabled(), 'permission save did not unlock after a successful baseline read');

        await page.getByRole('button', { name: '撤销读', exact: true }).click();
        const revokeDialog = page.getByRole('dialog', { name: '撤销密钥' });
        await revokeDialog.waitFor({ state: 'visible' });
        await revokeDialog.getByRole('button', { name: '取消', exact: true }).click();
        const persisted = await permissionsFromRealApi();
        assert(persisted.task_read === true && persisted.data_read === true && persisted.data_scope === 'selected', 'real permissions changed after a read retry without user save');
        assert(browserPermissionPosts === 0 && browserKeyPosts === 0, 'the browser issued a permission or key mutation during the baseline guard flow');
        entry.assertions = {
          four_key_controls_disabled_while_initial_get_pending_and_failed: true,
          permissions_and_save_disabled_until_baseline_exists: true,
          exactly_one_re_read_entry: true,
          retry_applied_real_task_data_and_selected_scope_baseline: true,
          four_key_controls_unlocked_after_retry: true,
          revoke_action_can_open_and_cancel_confirmation: true,
          no_browser_permission_or_key_mutations: true,
          real_permissions_unchanged: true,
        };
        entry.request_boundary = {
          initial_browser_settings_get: 'synthetic 503 injected after a controlled pending interval',
          retry_settings_get: 'real isolated API GET',
          browser_permission_posts: browserPermissionPosts,
          browser_key_posts: browserKeyPosts,
          isolated_api_setup: { permission_baseline_posts: 1, synthetic_keys_created: seededTokenKinds.length },
          real_model_calls: 0,
        };
      });
    } finally {
      if (releaseInitialSettingsFailure) releaseInitialSettingsFailure();
      if (releaseSettingsRetry) releaseSettingsRetry();
      await context.unroute(initialSettingsRoute).catch(() => undefined);
      const cleanup = { isolated_keys_revoked: [], original_permissions_restored: false };
      for (const kind of seededTokenKinds) {
        const revoke = await fetch(`${baseOrigin}/api/v1/integration/settings/keys/revoke`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind }),
        }).catch(() => undefined);
        cleanup.isolated_keys_revoked.push({ kind, ok: Boolean(revoke?.ok) });
      }
      const originalPermissionBaseline = {
        data_scope: originalIntegrationSettings.data_scope ?? 'all',
        table_ids: originalIntegrationSettings.table_ids ?? [],
        task_read: originalIntegrationSettings.task_read ?? false,
        template_read: originalIntegrationSettings.template_read ?? false,
        result_read: originalIntegrationSettings.result_read ?? false,
        data_read: originalIntegrationSettings.data_read ?? false,
        task_control: originalIntegrationSettings.task_control ?? false,
        write_enabled: originalIntegrationSettings.write_enabled ?? false,
        file_access: originalIntegrationSettings.file_access ?? false,
        file_roots: originalIntegrationSettings.file_roots ?? [],
      };
      const restore = await fetch(`${baseOrigin}/api/v1/integration/settings/permissions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(originalPermissionBaseline),
      }).catch(() => undefined);
      cleanup.original_permissions_restored = Boolean(restore?.ok);
      const baselineEntry = report.checks.find((check) => check.id === 'MCP-INITIAL-BASELINE-GUARD');
      if (baselineEntry) baselineEntry.isolated_api_cleanup = cleanup;
    }

    const pendingGenerateRoute = '**/api/v1/templates/generate';
    const releaseGenerations = [];
    const releasedGenerations = new Set();
    let generationRequests = 0;
    await context.route(pendingGenerateRoute, async (route) => {
      generationRequests += 1;
      report.injected_requests.synthetic_template_generations += 1;
      const requestNumber = generationRequests;
      await new Promise((resolve) => { releaseGenerations[requestNumber - 1] = () => resolve(); });
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          name: requestNumber === 1 ? '旧请求合成草稿' : '新请求合成草稿',
          description: '由浏览器路由注入，不是真实模型生成。',
          fields: [{ key: requestNumber === 1 ? 'old_synthetic_field' : 'new_synthetic_field', label: requestNumber === 1 ? '旧合成字段' : '新合成字段', section: 'header', example: '', value_type: 'text' }],
          rule_suggestions: [],
        }),
      }).catch(() => undefined); // A stopped browser fetch may already have closed this intercepted request.
    });
    await runCheck('TEMPLATE-GENERATE-STOP-WAITING', [
      'open Templates > AI 生成模板 and submit synthetic request A through an intercepted browser route',
      'stop waiting and return to editing; preserve the requirement and disclose that server-side work may continue',
      'submit synthetic request B, then release A late; verify B remains pending and no A draft is applied',
      'release B; verify only B becomes the editable draft, discard it, and confirm the real template list did not change',
    ], async (page, entry) => {
      const before = await getJson(`${baseOrigin}/api/v1/templates`);
      const beforeIds = before.map((template) => template.id).sort();
      await clickNav(page, '模板');
      await page.getByRole('heading', { name: '模板管理' }).waitFor({ state: 'visible' });
      await page.getByRole('button', { name: 'AI 生成模板', exact: true }).click();
      const dialog = page.getByRole('dialog', { name: '智能创建提取模板' });
      await dialog.waitFor({ state: 'visible' });
      await page.setViewportSize({ width: 390, height: 844 });
      const requirement = dialog.getByLabel('需求描述');
      await requirement.fill('合成审计请求 A；不调用真实模型');
      await dialog.getByRole('button', { name: '生成模板', exact: true }).click();
      await dialog.getByText('生成中，请稍候…', { exact: true }).waitFor({ state: 'visible' });
      await waitFor(() => generationRequests === 1, 'injected synthetic generation request A');
      const stopButton = dialog.getByRole('button', { name: '停止等待并返回编辑', exact: true });
      const stopBox = await stopButton.boundingBox();
      assert(stopBox && stopBox.width > 0 && stopBox.x >= 0 && stopBox.x + stopBox.width <= 390, 'stop-waiting action is unreachable or clipped at a narrow viewport');
      await stopButton.click();
      await dialog.getByRole('button', { name: '生成模板', exact: true }).waitFor({ state: 'visible' });
      assert(await requirement.inputValue() === '合成审计请求 A；不调用真实模型', 'stopping did not preserve the current requirement');
      assert(await dialog.getByRole('status').filter({ hasText: '模型服务可能仍在处理刚才的请求' }).count() === 1, 'stop-waiting notice did not disclose that remote work may continue');

      await requirement.fill('合成审计请求 B；不调用真实模型');
      await dialog.getByRole('button', { name: '生成模板', exact: true }).click();
      await dialog.getByText('生成中，请稍候…', { exact: true }).waitFor({ state: 'visible' });
      await waitFor(() => generationRequests === 2, 'injected synthetic generation request B');
      releaseGenerations[0]();
      releasedGenerations.add(0);
      await dialog.getByText('正在分析样例文件，请稍候…', { exact: true }).waitFor({ state: 'visible' });
      assert(await dialog.getByLabel('模板名称').count() === 0, 'late A response replaced B pending state with a draft');
      assert(await dialog.getByRole('button', { name: '停止等待并返回编辑', exact: true }).isEnabled(), 'late A response cleared B loading state');

      releaseGenerations[1]();
      releasedGenerations.add(1);
      await dialog.getByRole('button', { name: '保存模板', exact: true }).waitFor({ state: 'visible' });
      assert(await dialog.getByLabel('模板名称').inputValue() === '新请求合成草稿', 'the current B result was not applied');
      await dialog.getByRole('button', { name: '关闭', exact: true }).click();
      const discard = page.getByRole('dialog', { name: '模板尚未保存' });
      await discard.waitFor({ state: 'visible' });
      await discard.getByRole('button', { name: '不保存并关闭', exact: true }).click();
      await dialog.waitFor({ state: 'detached' });
      const after = await getJson(`${baseOrigin}/api/v1/templates`);
      assert(JSON.stringify(after.map((template) => template.id).sort()) === JSON.stringify(beforeIds), 'discarded synthetic generation changed the real template list');
      entry.assertions = {
        stop_waiting_returns_to_editor_without_cancelling_remote_claim: true,
        requirement_preserved_after_stop: true,
        late_A_cannot_replace_or_end_B: true,
        only_current_B_draft_applied: true,
        synthetic_draft_discarded_without_persistence: true,
      };
      entry.request_boundary = { injected_generation_requests: generationRequests, real_generation_requests: 0, real_model_calls: 0, real_template_mutations: 0 };
    });
    releaseGenerations.forEach((release, index) => {
      if (release && !releasedGenerations.has(index)) release();
    });
    await context.unroute(pendingGenerateRoute);

    report.status = report.checks.length > 0 && report.checks.every((check) => check.status === 'passed') ? 'passed_in_scope' : 'failed';
    report.real_model_calls = 0;
    report.external_browser_requests = report.external_browser_requests;
    report.completed_at = new Date().toISOString();
    writeReport();
    console.log(JSON.stringify({ status: report.status, checks: report.checks.map(({ id, status }) => ({ id, status })), evidence: path.relative(project, evidenceRoot).replaceAll('\\', '/') }));
    if (report.status !== 'passed_in_scope') process.exitCode = 1;
  } catch (error) {
    report.status = 'failed';
    report.failure = { name: error && typeof error.name === 'string' ? error.name : 'Error', reason: error && typeof error.message === 'string' ? error.message.slice(0, 180) : 'harness setup failed' };
    report.completed_at = new Date().toISOString();
    writeReport();
    // Do not print untrusted browser/API bodies; evidence has the scoped failure stage.
    console.error(`Deep UX verification failed; preserve evidence at ${path.relative(project, evidenceRoot).replaceAll('\\', '/')}`);
    process.exitCode = 1;
  } finally {
    if (context) await context.close().catch(() => undefined);
    if (browser) await browser.close().catch(() => undefined);
    if (server && server.exitCode === null) {
      server.kill();
      await Promise.race([once(server, 'exit'), new Promise((resolve) => setTimeout(resolve, 5000))]);
    }
    if (serverLog) serverLog.end();
  }
}

main();
