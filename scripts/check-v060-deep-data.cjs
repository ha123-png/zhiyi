// Deep interaction audit for the data warehouse, file history, and backup UI.
// Runs only against a fresh .local fixture DB and headless Chromium.
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const crypto = require('node:crypto');
const { once } = require('node:events');
const { randomUUID } = require('node:crypto');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const project = path.resolve(__dirname, '..');
const runId = process.env.ZHIYI_DEEP_DATA_RUN || `${new Date().toISOString().slice(0, 10).replaceAll('-', '')}-${randomUUID().slice(0, 8)}`;
assert(/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(runId), `Run ID must be a single safe path segment: ${runId}`);
const checkpointRunId = process.env.ZHIYI_DEEP_DATA_CHECKPOINT || null;
if (checkpointRunId) assert(/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(checkpointRunId), `Checkpoint ID must be a single safe path segment: ${checkpointRunId}`);
const evidence = path.join(project, '.local', 'evidence', 'v0.6.0', '2026-09-30-deep-data', runId);
const runtime = path.join(project, '.local', 'tmp', '2026-09-30-deep-data', runId);
const checkpointEvidence = checkpointRunId ? path.join(project, '.local', 'evidence', 'v0.6.0', '2026-09-30-deep-data', checkpointRunId) : null;
const checkpointRuntime = checkpointRunId ? path.join(project, '.local', 'tmp', '2026-09-30-deep-data', checkpointRunId) : null;
const dataDir = path.join(runtime, 'data');
const fixtureDir = path.join(runtime, 'fixtures');
const outputDir = path.join(evidence, 'browser');
const apiPython = process.env.ZHIYI_TEST_PYTHON || path.join(project, 'apps', 'api', '.venv', 'Scripts', 'python.exe');
const prepareSource = path.join(project, 'scripts', 'prepare-source-acceptance.py');
const prepareDeep = path.join(project, 'scripts', 'prepare-v060-deep-data.py');

for (const candidate of [evidence, runtime]) {
  const relative = path.relative(path.join(project, '.local'), candidate);
  assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative), `Refusing non-.local run path: ${candidate}`);
}
assert(!fs.existsSync(evidence), `Evidence run already exists; choose a fresh run ID: ${evidence}`);
assert(!fs.existsSync(runtime), `Runtime data run already exists; refusing to reuse it: ${runtime}`);
let checkpointReport = null;
let priorMerge = null;
let priorSourceCards = null;
let priorHistory = null;
let priorHistoryDownloadObservation = null;
let priorBulkDeleteObservation = null;
const checkpointEvidenceMap = new Map();
if (checkpointRunId) {
  for (const candidate of [checkpointEvidence, checkpointRuntime]) {
    const relative = path.relative(path.join(project, '.local'), candidate);
    assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative), `Refusing checkpoint outside .local: ${candidate}`);
  }
  const checkpointReportPath = path.join(checkpointEvidence, 'report.json');
  assert(fs.existsSync(checkpointReportPath) && fs.existsSync(path.join(checkpointRuntime, 'data', 'document-pipeline.db')),
    `Checkpoint evidence or database is missing: ${checkpointRunId}`);
  checkpointReport = JSON.parse(fs.readFileSync(checkpointReportPath, 'utf8'));
  assert.equal(checkpointReport.run_id, checkpointRunId, 'Checkpoint run ID must match its report.');
  const reusableIds = ['table-search-selection-pages', 'xlsx-sheet-cancel-retry', 'custom-column-clear-delete', 'dynamic-group-export'];
  for (const id of reusableIds) assert(checkpointReport.scenarios?.some(item => item.id === id && item.passed), `Checkpoint lacks passed scenario ${id}.`);
  priorMerge = checkpointReport.scenarios?.find(item => item.id === 'merge-cancel-double-click') ?? null;
  priorSourceCards = checkpointReport.scenarios?.find(item => item.id === 'source-card-pages-selection-cancel-and-export') ?? null;
  priorHistory = checkpointReport.scenarios?.find(item => item.id === 'history-search-template-time-pagination-and-download') ?? null;
  const priorBulkDelete = checkpointReport.scenarios?.find(item => item.id === 'table-bulk-delete-cancel-confirm');
  const knownMergeOracleStop = priorMerge && !priorMerge.passed && priorMerge.error?.includes('assert(codes.includes');
  const knownSourceCardRaceStop = priorMerge?.passed && priorSourceCards && !priorSourceCards.passed && priorSourceCards.error?.includes('已选');
  const knownHistoryLocatorStop = priorSourceCards?.passed && priorHistory && !priorHistory.passed && priorHistory.error?.includes('strict mode violation');
  const knownHistoryDownloadObserverStop = priorSourceCards?.passed && priorHistory && !priorHistory.passed &&
    priorHistory.error?.includes('page.waitForResponse: Timeout 30000ms exceeded');
  if (knownHistoryDownloadObserverStop) {
    const expectedTask = checkpointReport.fixture_summary?.tasks?.find(task => task.filename === 'DD-HISTORY-12.txt');
    const downloadPath = path.join(checkpointEvidence, 'browser', '07-history-original.txt');
    const tracePath = path.join(checkpointEvidence, 'trace.zip');
    assert(expectedTask && fs.existsSync(downloadPath) && fs.existsSync(tracePath),
      'History observer-stop checkpoint is missing its exact download or trace evidence.');
    const traceReader = String.raw`import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    events = [json.loads(line) for line in archive.read('trace.trace').decode('utf-8').splitlines() if line.strip()]
downloads = [event for event in events if event.get('type') == 'event' and event.get('class') == 'BrowserContext' and event.get('method') == 'download']
print(json.dumps([event.get('params', {}) for event in downloads], ensure_ascii=False))`;
    const inspectedTrace = spawnSync(apiPython, ['-c', traceReader, tracePath], {
      cwd: project, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' },
    });
    assert.equal(inspectedTrace.status, 0, `Could not inspect the prior Playwright download trace: ${inspectedTrace.stderr}`);
    const downloadEvents = JSON.parse(inspectedTrace.stdout.trim());
    const matchingDownload = downloadEvents.find(event => {
      try {
        const url = new URL(event.url);
        return url.pathname === `/api/v1/tasks/${expectedTask.task_id}/file` && event.suggestedFilename === expectedTask.filename;
      } catch { return false; }
    });
    assert(matchingDownload, 'Prior Playwright trace does not prove a UI-triggered download from the expected original-file API route.');
    const downloadBytes = fs.readFileSync(downloadPath);
    assert.equal(downloadBytes.length, expectedTask.size_bytes, 'Prior UI download byte length differs from the fixture oracle.');
    assert.equal(sha256(downloadBytes), expectedTask.sha256, 'Prior UI download SHA-256 differs from the fixture oracle.');
    priorHistoryDownloadObservation = {
      task_id: expectedTask.task_id,
      filename: expectedTask.filename,
      url: matchingDownload.url,
      size_bytes: downloadBytes.length,
      sha256: sha256(downloadBytes),
      trace_event: 'BrowserContext.download',
      response_waiter_failure: priorHistory.error,
    };
  }
  const knownBulkDeleteResponseObserverStop = priorHistory?.passed && priorBulkDelete && !priorBulkDelete.passed &&
    priorBulkDelete.error?.includes('page.waitForResponse: Timeout 30000ms exceeded');
  if (knownBulkDeleteResponseObserverStop) {
    const tracePath = path.join(checkpointEvidence, 'trace.zip');
    const screenshotPath = path.join(checkpointEvidence, 'browser', 'table-bulk-delete-cancel-confirm-failure.png');
    assert(fs.existsSync(tracePath) && fs.existsSync(screenshotPath), 'Bulk-delete observer-stop checkpoint is missing trace or final-state screenshot evidence.');
    const traceReader = String.raw`import json, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    resources = []
    for line in archive.read('trace.network').decode('utf-8').splitlines():
        if not line.strip(): continue
        entry = json.loads(line)
        snapshot = entry.get('snapshot', {})
        request = snapshot.get('request', {})
        response = snapshot.get('response', {})
        if request.get('method') != 'DELETE' or response.get('status') != 204: continue
        post_data = request.get('postData', {})
        payload = json.loads(archive.read(post_data['_file']).decode('utf-8')) if post_data.get('_file') else {}
        resources.append({'url': request.get('url'), 'status': response.get('status'), 'row_ids': payload.get('row_ids', []), 'expected_versions': payload.get('expected_versions', {})})
print(json.dumps(resources, ensure_ascii=False))`;
    const inspectedTrace = spawnSync(apiPython, ['-c', traceReader, tracePath], {
      cwd: project, encoding: 'utf8', env: { ...process.env, PYTHONUTF8: '1' },
    });
    assert.equal(inspectedTrace.status, 0, `Could not inspect the prior Playwright delete trace: ${inspectedTrace.stderr}`);
    const deleteEvents = JSON.parse(inspectedTrace.stdout.trim());
    assert.equal(deleteEvents.length, 1, 'Expected exactly one successful bulk-delete request in the prior trace.');
    const match = deleteEvents[0];
    const url = new URL(match.url);
    const tableMatch = url.pathname.match(/^\/api\/v1\/tables\/([^/]+)\/rows$/);
    assert(tableMatch && url.hostname === '127.0.0.1', 'Prior successful deletion did not target the isolated local data-table API.');
    assert.equal(match.status, 204);
    assert.equal(match.row_ids.length, 2);
    assert.equal(new Set(match.row_ids).size, 2);
    assert.deepEqual(Object.keys(match.expected_versions).sort(), match.row_ids.map(String).sort());
    priorBulkDeleteObservation = {
      table_id: tableMatch[1],
      url: match.url,
      status: match.status,
      row_ids: match.row_ids,
      expected_versions: match.expected_versions,
      response_waiter_failure: priorBulkDelete.error,
      screenshot: screenshotPath,
      trace: tracePath,
    };
  }
  assert(knownMergeOracleStop || knownSourceCardRaceStop || knownHistoryLocatorStop || priorHistoryDownloadObservation || priorBulkDeleteObservation,
    'Checkpoint must stop only at a known merge oracle, source-card loading race, history locator collision, or independently verifiable history/bulk response-observer mismatch.');
  assert(fs.existsSync(path.join(checkpointRuntime, 'web-dist', 'index.html')), 'Checkpoint has no frozen frontend build.');
}
fs.mkdirSync(outputDir, { recursive: true });
fs.mkdirSync(runtime, { recursive: true });
const report = {
  run_id: runId,
  passed: false,
  real_model_calls: 0,
  browser: 'Playwright Chromium, headless',
  source_branch: null,
  source_head: null,
  frontend_asset: null,
  evidence_dir: evidence,
  runtime_dir: runtime,
  scenarios: [],
  page_errors: [],
  local_data_http_sequence: [],
  limitations: [
    '合成数据与本机真实 HTTP API；不调用真实模型，不代表安装包或物理断电。',
    '界面“字段筛选”是列显示开关；产品当前没有可见排序控件，本次不把它记作行筛选或排序通过。',
  ],
};

function inventoryTree(root) {
  const files = [];
  const visit = (directory, prefix = '') => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      const relative = path.posix.join(prefix, entry.name);
      assert(!entry.isSymbolicLink(), `Refusing symbolic link inside audit fixture tree: ${absolute}`);
      if (entry.isDirectory()) visit(absolute, relative);
      else if (entry.isFile()) files.push({ path: relative, size_bytes: fs.statSync(absolute).size, sha256: sha256(fs.readFileSync(absolute)) });
    }
  };
  visit(root);
  return files.sort((left, right) => left.path.localeCompare(right.path));
}

if (checkpointReport) {
  for (const entry of fs.readdirSync(checkpointRuntime, { withFileTypes: true })) {
    assert(!entry.isSymbolicLink(), `Refusing checkpoint symbolic link: ${entry.name}`);
    fs.cpSync(path.join(checkpointRuntime, entry.name), path.join(runtime, entry.name), { recursive: true, errorOnExist: true, force: false });
  }
  const reusedEvidence = [];
  const sourceBrowser = path.join(checkpointEvidence, 'browser');
  const reusableItems = [
    { id: 'table-search-selection-pages', candidates: ['01-table-search-selection.png', 'reused-01-table-search-selection.png'], copy_as: 'reused-01-table-search-selection.png' },
    { id: 'xlsx-sheet-cancel-retry', candidates: ['02-xlsx-sheet-retry.png', 'reused-02-xlsx-sheet-retry.png'], copy_as: 'reused-02-xlsx-sheet-retry.png' },
    { id: 'custom-column-clear-delete', candidates: ['03-custom-column-constraints.png', 'reused-03-custom-column-constraints.png'], copy_as: 'reused-03-custom-column-constraints.png' },
    { id: 'dynamic-group-export', candidates: ['04-gamma-group-export.png', 'reused-04-gamma-group-export.png'], copy_as: 'reused-04-gamma-group-export.png' },
    { id: 'dynamic-group-export-download', candidates: ['04-gamma-group.xlsx', 'reused-04-gamma-group.xlsx'], copy_as: 'reused-04-gamma-group.xlsx' },
  ];
  if (priorMerge?.passed) reusableItems.push({ id: 'merge-cancel-double-click', candidates: ['05-merge-cancel-double-click.png', 'reused-05-merge-cancel-double-click.png'], copy_as: 'reused-05-merge-cancel-double-click.png' });
  else reusableItems.push({ id: 'merge-cancel-double-click', candidates: ['merge-cancel-double-click-failure.png'], copy_as: 'reused-merge-cancel-double-click-failure.png' });
  if (priorSourceCards?.passed) reusableItems.push({ id: 'source-card-pages-selection-cancel-and-export', candidates: ['06-file-card-multipage.png', 'reused-06-file-card-multipage.png'], copy_as: 'reused-06-file-card-multipage.png' });
  if (priorHistoryDownloadObservation || priorHistory?.passed) {
    reusableItems.push({ id: 'history-search-template-time-pagination-and-download', candidates: ['07-history-filter-pagination.png', 'history-search-template-time-pagination-and-download-failure.png', 'reused-07-history-filter-pagination-failure.png', 'reused-07-history-filter-pagination.png'], copy_as: 'reused-07-history-filter-pagination.png' });
    reusableItems.push({ id: 'history-original-download', candidates: ['07-history-original.txt', 'reused-07-history-original.txt'], copy_as: 'reused-07-history-original.txt' });
  }
  if (priorBulkDeleteObservation) {
    reusableItems.push({ id: 'table-bulk-delete-cancel-confirm', candidates: ['table-bulk-delete-cancel-confirm-failure.png'], copy_as: 'reused-07b-table-bulk-delete-confirm-failure.png' });
    const source = priorBulkDeleteObservation.trace;
    const destination = path.join(evidence, 'reused-bulk-delete-trace.zip');
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    const bytes = fs.readFileSync(destination);
    reusedEvidence.push({ source, copied_to: destination, size_bytes: bytes.length, sha256: sha256(bytes) });
  }
  if (['05-merge-result-checkpoint.png', 'reused-05-merge-result-checkpoint.png'].some(name => fs.existsSync(path.join(sourceBrowser, name))))
    reusableItems.push({ id: 'merge-result-oracle', candidates: ['05-merge-result-checkpoint.png', 'reused-05-merge-result-checkpoint.png'], copy_as: 'reused-05-merge-result-checkpoint.png' });
  for (const item of reusableItems) {
    const sourceName = item.candidates.find(name => fs.existsSync(path.join(sourceBrowser, name)));
    assert(sourceName, `Checkpoint evidence file is missing for ${item.id}: ${item.candidates.join(', ')}`);
    const source = path.join(sourceBrowser, sourceName);
    const destination = path.join(outputDir, item.copy_as);
    fs.copyFileSync(source, destination, fs.constants.COPYFILE_EXCL);
    const bytes = fs.readFileSync(destination);
    reusedEvidence.push({ source, copied_to: destination, size_bytes: bytes.length, sha256: sha256(bytes) });
  if (!checkpointEvidenceMap.has(item.id)) checkpointEvidenceMap.set(item.id, { source, copied_to: destination });
  }
  report.checkpoint_parent = {
    run_id: checkpointRunId,
    evidence_report: path.join(checkpointEvidence, 'report.json'),
    evidence_report_sha256: sha256(fs.readFileSync(path.join(checkpointEvidence, 'report.json'))),
    reused_passed_scenarios: checkpointReport.scenarios.filter(item => item.passed).map(item => item.id),
    independently_verified_partial_scenarios: [
      ...(priorHistoryDownloadObservation ? [{
      id: 'history-search-template-time-pagination-and-download',
      observation: priorHistoryDownloadObservation,
      source_evidence: path.join(checkpointEvidence, 'browser', '07-history-original.txt'),
      source_trace: path.join(checkpointEvidence, 'trace.zip'),
      }] : []),
      ...(priorBulkDeleteObservation ? [{
        id: 'table-bulk-delete-cancel-confirm',
        observation: priorBulkDeleteObservation,
        source_screenshot: priorBulkDeleteObservation.screenshot,
        source_trace: priorBulkDeleteObservation.trace,
      }] : []),
    ],
    source_failed_scenarios: checkpointReport.scenarios.filter(item => !item.passed).map(item => item.id),
    runtime_file_manifest: inventoryTree(checkpointRuntime),
    copied_runtime_file_manifest: inventoryTree(runtime),
    reused_evidence: reusedEvidence,
  };
  assert.deepEqual(report.checkpoint_parent.runtime_file_manifest, report.checkpoint_parent.copied_runtime_file_manifest,
    'Checkpoint copy must preserve the source fixture, frozen build, and database bytes exactly.');
}

function runProcess(executable, args, label, env = process.env) {
  const logPath = path.join(evidence, `${label}.log`);
  return new Promise((resolve, reject) => {
    const log = fs.createWriteStream(logPath);
    const child = spawn(executable, args, { cwd: project, env, windowsHide: true });
    child.stdout.pipe(log);
    child.stderr.pipe(log);
    child.on('error', reject);
    child.on('close', code => {
      log.end(() => {
        if (code === 0) return resolve(logPath);
        reject(new Error(`${label} exited with ${code}; see ${logPath}\n${fs.readFileSync(logPath, 'utf8').slice(-7000)}`));
      });
    });
  });
}

function isolatedEnv(extra = {}) {
  return {
    ...process.env,
    PYTHONUTF8: '1',
    ZHIYI_OFFLINE: '1',
    DATA_DIR: dataDir,
    DOCUMENT_PIPELINE_DATA_DIR: dataDir,
    DOCUMENT_PIPELINE_QUEUE_DB: path.join(runtime, 'queue.db'),
    DOCUMENT_PIPELINE_WORKER_HEARTBEAT: path.join(runtime, 'worker-heartbeat.json'),
    ...extra,
  };
}

function python(args, label) {
  return new Promise((resolve, reject) => {
    const logPath = path.join(evidence, `${label}.log`);
    const log = fs.createWriteStream(logPath);
    const child = spawn(apiPython, args, {
      cwd: project,
      windowsHide: true,
      env: {
        ...process.env,
        PYTHONUTF8: '1',
        ZHIYI_OFFLINE: '1',
        DATA_DIR: dataDir,
        DOCUMENT_PIPELINE_DATA_DIR: dataDir,
        DOCUMENT_PIPELINE_QUEUE_DB: path.join(runtime, 'queue.db'),
      },
    });
    child.stdout.pipe(log);
    child.stderr.pipe(log);
    child.on('error', reject);
    child.on('close', code => {
      log.end(() => {
        const output = fs.existsSync(logPath) ? fs.readFileSync(logPath, 'utf8') : '';
        if (code === 0) return resolve(output.trim());
        reject(new Error(`${label} exited with ${code}; see ${logPath}\n${output.slice(-7000)}`));
      });
    });
  });
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const socket = net.createServer();
    socket.once('error', reject);
    socket.listen(0, '127.0.0.1', async () => {
      const port = socket.address().port;
      await new Promise(done => socket.close(done));
      resolve(port);
    });
  });
}

function sha256(buffer) {
  return crypto.createHash('sha256').update(buffer).digest('hex');
}

function lastJsonLine(output, label) {
  const candidates = output.split(/\r?\n/).map(line => line.trim()).filter(line => line.startsWith('{'));
  assert(candidates.length > 0, `No JSON object in ${label} output.`);
  try { return JSON.parse(candidates[candidates.length - 1]); }
  catch (error) { throw new Error(`Could not parse final JSON object from ${label}: ${error.message}`); }
}

async function main() {
  assert(fs.existsSync(apiPython), `Python environment not found: ${apiPython}`);
  assert(fs.existsSync(path.join(project, 'apps', 'web', 'dist', 'index.html')), 'Build the frontend from the project root before this browser audit.');

  if (checkpointReport) {
    report.fixture_summary = checkpointReport.fixture_summary;
    report.fixture_tables = checkpointReport.fixture_tables;
    fs.copyFileSync(path.join(runtime, 'deep-fixtures.json'), path.join(evidence, 'deep-fixtures.json'), fs.constants.COPYFILE_EXCL);
    fs.copyFileSync(path.join(runtime, 'data', 'source-fixtures-report.json'), path.join(evidence, 'source-fixtures-report.json'), fs.constants.COPYFILE_EXCL);
  } else {
    await runProcess(apiPython, [prepareSource, '--data-dir', dataDir, '--files-dir', fixtureDir], 'fixtures-source', isolatedEnv());
    const deepFixtureJson = await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--report', path.join(runtime, 'deep-fixtures.json')], 'fixtures-deep');
    report.fixture_summary = lastJsonLine(deepFixtureJson, 'deep fixtures');
    fs.copyFileSync(path.join(runtime, 'data', 'source-fixtures-report.json'), path.join(evidence, 'source-fixtures-report.json'));
    fs.copyFileSync(path.join(runtime, 'deep-fixtures.json'), path.join(evidence, 'deep-fixtures.json'));
    fs.copyFileSync(path.join(fixtureDir, 'DD-multi-sheet-import.xlsx'), path.join(evidence, 'stimulus-multi-sheet.xlsx'));
    fs.writeFileSync(path.join(fixtureDir, 'DD-new-table.csv'), '\uFEFF记录号,分类,金额,有效,说明\r\nDD-NEW-TABLE-01,Gamma,19,true,从文件新建表夹具\r\n', 'utf8');
    fs.copyFileSync(path.join(fixtureDir, 'DD-new-table.csv'), path.join(evidence, 'stimulus-new-table.csv'));
  }
  if (checkpointReport?.source_collection_fixture) {
    report.source_collection_fixture = checkpointReport.source_collection_fixture;
  } else {
    const fileCollectionJson = await python([
      prepareDeep,
      '--data-dir', dataDir,
      '--files-dir', fixtureDir,
      '--seed-file-collection',
      '--report', path.join(runtime, 'source-card-fixtures.json'),
    ], 'fixtures-file-collection');
    report.source_collection_fixture = lastJsonLine(fileCollectionJson, 'source-card fixtures');
  }
  assert.equal(report.source_collection_fixture.real_model_calls, 0);
  assert.equal(report.source_collection_fixture.tasks.length, 8);
  if (!fs.existsSync(path.join(evidence, 'source-card-fixtures.json')))
    fs.copyFileSync(path.join(runtime, 'source-card-fixtures.json'), path.join(evidence, 'source-card-fixtures.json'), fs.constants.COPYFILE_EXCL);
  for (const task of report.source_collection_fixture.tasks) {
    const originalPath = path.join(fixtureDir, task.filename);
    const originalBytes = fs.readFileSync(originalPath);
    assert.equal(sha256(originalBytes), task.sha256, `Source-card fixture oracle mismatch for ${task.filename}.`);
    fs.copyFileSync(originalPath, path.join(evidence, `stimulus-${task.filename}`), fs.constants.COPYFILE_EXCL);
  }

  const envBase = {
    ...process.env,
    PYTHONUTF8: '1',
    ZHIYI_OFFLINE: '1',
    ZHIYI_VISUAL_OUT: outputDir,
    DATA_DIR: dataDir,
    DOCUMENT_PIPELINE_DATA_DIR: dataDir,
    DOCUMENT_PIPELINE_QUEUE_DB: path.join(runtime, 'queue.db'),
    DOCUMENT_PIPELINE_WORKER_HEARTBEAT: path.join(runtime, 'worker-heartbeat.json'),
  };
  delete envBase.ZHIYI_FAST;
  const port = await reservePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  envBase.ZHIYI_ACCEPTANCE_URL = baseUrl;
  envBase.ZHIYI_ACCEPTANCE_API = `${baseUrl}/api/v1`;
  envBase.ZHIYI_FIXTURE_DIR = path.join(runtime, 'pressure-files');
  const frozenWebDir = path.join(runtime, 'web-dist');
  if (!checkpointReport) fs.cpSync(path.join(project, 'apps', 'web', 'dist'), frozenWebDir, { recursive: true });
  const frozenIndex = fs.readFileSync(path.join(frozenWebDir, 'index.html'));
  report.frontend_asset = {
    frozen_web_dir: frozenWebDir,
    index_sha256: sha256(frozenIndex),
    assets: fs.readdirSync(path.join(frozenWebDir, 'assets')).sort(),
  };
  const serverLogPath = path.join(evidence, 'server.log');
  const serverLog = fs.createWriteStream(serverLogPath);
  const server = spawn(apiPython, [path.join(project, 'scripts', 'serve-verification.py'), '--data-dir', dataDir, '--port', String(port), '--web-dir', frozenWebDir], { cwd: project, env: envBase, windowsHide: true });
  server.stdout.pipe(serverLog);
  server.stderr.pipe(serverLog);
  let serverError;
  server.on('error', error => { serverError = error; });
  const stopServer = () => { if (server.exitCode === null) server.kill(); };
  process.once('SIGINT', stopServer);
  process.once('SIGTERM', stopServer);

  let browser;
  let page;
  let dbTemplate;
  let table;
  let tableBaseline;
  let mergeTable;
  let importWorkbookPath = path.join(fixtureDir, 'DD-multi-sheet-import.xlsx');
  let finalDbSnapshot;
  const pageErrors = report.page_errors;
  try {
    let ready = false;
    for (let attempt = 0; attempt < 80; attempt += 1) {
      if (serverError) throw serverError;
      if (server.exitCode !== null) throw new Error('Verification server stopped during startup.');
      try {
        const response = await fetch(`${baseUrl}/api/v1/templates`, { signal: AbortSignal.timeout(1000) });
        if (response.ok) { ready = true; break; }
      } catch { /* wait for local verification API */ }
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    assert(ready, `Isolated API startup timed out; see ${serverLogPath}`);

    const git = spawnSync('git', ['rev-parse', '--abbrev-ref', 'HEAD'], { cwd: project, encoding: 'utf8' });
    const head = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: project, encoding: 'utf8' });
    report.source_branch = git.stdout.trim();
    report.source_head = head.stdout.trim();
    const html = fs.readFileSync(path.join(frozenWebDir, 'index.html'), 'utf8');
    report.frontend_asset = [...html.matchAll(/src="([^"]+\.js)"/g)].map(match => match[1]);
    report.frontend_index_sha256 = sha256(fs.readFileSync(path.join(frozenWebDir, 'index.html')));
    report.runtime_api = baseUrl;

    browser = await chromium.launch({ ...(process.env.ZHIYI_BROWSER_CHANNEL ? { channel: process.env.ZHIYI_BROWSER_CHANNEL } : {}), headless: true });
    const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1366, height: 900 }, locale: 'zh-CN' });
    page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(String(error)));
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.pathname.includes('/source-cards') || url.pathname.endsWith('/export.json'))
        report.local_data_http_sequence.push({ event: 'request', method: request.method(), path: `${url.pathname}${url.search}` });
    });
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.pathname.includes('/source-cards') || url.pathname.endsWith('/export.json'))
        report.local_data_http_sequence.push({ event: 'response', status: response.status(), path: `${url.pathname}${url.search}` });
    });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    const skipOnboarding = page.getByRole('button', { name: '跳过引导' });
    if (await skipOnboarding.isVisible().catch(() => false)) await skipOnboarding.click();

    const api = async (method, route, data) => {
      const response = await page.request.fetch(`${baseUrl}/api/v1${route}`, { method, data });
      const body = response.status() === 204 ? null : await response.json().catch(() => null);
      if (!response.ok()) throw new Error(`${method} ${route}: ${response.status()} ${JSON.stringify(body)}`);
      return body;
    };
    const apiResponse = async (method, route, data) => page.request.fetch(`${baseUrl}/api/v1${route}`, { method, data });
    const saveShot = async name => page.screenshot({ path: path.join(outputDir, name), fullPage: true });
    const scenario = async (id, title, action, screenshot) => {
      const reused = checkpointReport?.scenarios.find(item => item.id === id && item.passed);
      if (reused) {
        report.scenarios.push({ ...reused, reused_from_run: checkpointRunId,
          reused_screenshot: checkpointEvidenceMap.get(id)?.copied_to ?? null,
          source_screenshot: checkpointEvidenceMap.get(id)?.source ?? path.join(checkpointEvidence, 'browser', screenshot) });
        return;
      }
      if (id === 'history-search-template-time-pagination-and-download' && priorHistoryDownloadObservation) {
        const response = await apiResponse('GET', `/tasks/${priorHistoryDownloadObservation.task_id}/file`);
        assert(response.ok(), `Fresh copied checkpoint API returned ${response.status()} for the verified original-file download.`);
        const responseBytes = await response.body();
        assert.equal(responseBytes.length, priorHistoryDownloadObservation.size_bytes);
        assert.equal(sha256(responseBytes), priorHistoryDownloadObservation.sha256,
          'Independent HTTP read from the copied checkpoint must match the exact UI-downloaded fixture bytes.');
        report.scenarios.push({
          id,
          title,
          passed: true,
          reused_from_run: checkpointRunId,
          started_at: new Date().toISOString(),
          reused_screenshot: checkpointEvidenceMap.get(id)?.copied_to ?? null,
          source_screenshot: checkpointEvidenceMap.get(id)?.source ?? null,
          details: {
            browser_ui_flow_preceded_response_waiter_timeout: true,
            prior_browser_download: priorHistoryDownloadObservation,
            independent_copied_checkpoint_http_sha256: sha256(responseBytes),
            filters_and_page_jump_preceded_download: true,
            response_observer_note: 'Playwright emitted BrowserContext.download for the exact API URL but did not surface the navigation download as a page response; byte identity was verified against the fixture and rechecked over HTTP on the isolated checkpoint copy.',
          },
        });
        return;
      }
      if (id === 'table-bulk-delete-cancel-confirm' && priorBulkDeleteObservation) {
        const after = await api('GET', `/tables/${priorBulkDeleteObservation.table_id}?page=1&page_size=500`);
        assert.equal(after.row_count, 1, 'The copied checkpoint must retain only the unselected sentinel row.');
        assert.equal(after.rows.length, 1);
        const keepAfter = after.rows[0];
        assert.deepEqual(keepAfter.values, {
          code: 'DD-BULK-KEEP-03',
          bucket: 'untouched-sentinel',
          amount: 33,
          active: true,
          note: 'must remain byte-for-byte equivalent in values',
        }, 'The unselected row must preserve every field value after the confirmed bulk delete.');
        const deletionRevisions = await Promise.all(priorBulkDeleteObservation.row_ids.map(async rowId => {
          const revisions = await api('GET', `/tables/${priorBulkDeleteObservation.table_id}/rows/${rowId}/revisions?page=1&page_size=50`);
          const deletion = revisions.find(revision => revision.operation === 'delete');
          assert(deletion, `Deleted row ${rowId} has no retained deletion revision.`);
          assert.equal(deletion.after, null);
          return { row_id: rowId, before: deletion.before, operation: deletion.operation, version: deletion.version };
        }));
        assert.deepEqual(deletionRevisions.map(revision => revision.before?.code).sort(),
          ['DD-BULK-DELETE-01', 'DD-BULK-DELETE-02'],
          'The successful DELETE payload must correspond to the two selected synthetic targets.');
        report.scenarios.push({
          id,
          title,
          passed: true,
          reused_from_run: checkpointRunId,
          started_at: new Date().toISOString(),
          reused_screenshot: checkpointEvidenceMap.get(id)?.copied_to ?? null,
          source_screenshot: checkpointEvidenceMap.get(id)?.source ?? null,
          details: {
            prior_browser_cancel_assertions_completed_before_confirm: true,
            prior_browser_delete_request: priorBulkDeleteObservation,
            delete_response_status: priorBulkDeleteObservation.status,
            deleted_revisions: deletionRevisions,
            copied_checkpoint_remaining_rows: after.row_count,
            unselected_row: { id: keepAfter.id, values: keepAfter.values, unchanged: true },
            exact_impact_set_verified: true,
            response_observer_note: 'The earlier response waiter accidentally used the main fixture table ID after selecting the dedicated bulk-delete table. The trace and the isolated database prove the UI action itself returned 204 and affected only the two selected IDs; the checkpoint copy was re-read over HTTP and its retained delete revisions verified.',
          },
        });
        return;
      }
      const started = new Date().toISOString();
      try {
        const details = await action();
        if (screenshot) await saveShot(screenshot);
        report.scenarios.push({ id, title, passed: true, started_at: started, details: details ?? null });
      } catch (error) {
        await page.screenshot({ path: path.join(outputDir, `${id}-failure.png`), fullPage: true }).catch(() => {});
        let diagnostics = null;
        if (id === 'source-card-pages-selection-cancel-and-export') {
          diagnostics = await page.evaluate(() => {
            const collection = document.querySelector('.file-collection');
            return {
              collection_busy: collection?.getAttribute('aria-busy') ?? null,
              page_text: collection?.querySelector('.table-footer .pagination span')?.textContent?.trim() ?? null,
              visible_cards: [...(collection?.querySelectorAll('.file-data-card') ?? [])].map(card => ({
                filename: card.querySelector('.file-card-heading button')?.textContent?.trim() ?? null,
                checked: card.querySelector('input[type="checkbox"]')?.checked ?? null,
                disabled: card.querySelector('input[type="checkbox"]')?.disabled ?? null,
              })),
              selected_hint: document.querySelector('.row-selected-hint')?.textContent?.trim() ?? null,
            };
          }).catch(reason => ({ diagnostic_error: String(reason) }));
        }
        report.scenarios.push({ id, title, passed: false, started_at: started, error: String(error), diagnostics });
        throw error;
      }
    };

    if (!checkpointReport) {
      const tableTemplateName = `DD-深度数据-${runId}`;
      dbTemplate = await api('POST', '/templates', {
        name: tableTemplateName,
        description: '深度交互审计隔离夹具。',
        extra_instructions: '',
        fields: [
          { key: 'code', label: '记录号', section: 'item', value_type: 'text' },
          { key: 'bucket', label: '分类', section: 'item', value_type: 'text' },
          { key: 'amount', label: '金额', section: 'item', value_type: 'number' },
          { key: 'active', label: '有效', section: 'item', value_type: 'boolean' },
          { key: 'note', label: '说明', section: 'item', value_type: 'text' },
        ],
        validation_rules: [],
        deterministic_rules: [],
        output_mapping: {},
      });
      table = await api('POST', '/tables', { name: `DD-仓库交互-${runId}`, template_key: dbTemplate.id });
      const initialRows = [];
      for (let index = 1; index <= 27; index += 1) {
        initialRows.push(await api('POST', `/tables/${table.id}/rows`, { values: {
          code: `DD-ROW-${String(index).padStart(2, '0')}`,
          bucket: index <= 14 ? 'Alpha' : 'Beta',
          amount: index === 1 ? 0 : index * 1.25,
          active: index % 2 === 0,
          note: index === 1 ? 'zero + false must stay explicit' : `隔离说明 ${index}`,
        } }));
      }
      tableBaseline = { row_count: 27, first_row_id: initialRows[0].id, rows: initialRows };
      mergeTable = await api('POST', '/tables', { name: `DD-合并源表-${runId}`, template_key: dbTemplate.id });
      await api('POST', `/tables/${mergeTable.id}/rows`, { values: { code: 'DD-MERGE-01', bucket: 'Merge', amount: 41, active: true, note: 'merge source one' } });
      await api('POST', `/tables/${mergeTable.id}/rows`, { values: { code: 'DD-MERGE-02', bucket: 'Merge', amount: 42, active: false, note: 'merge source two' } });
      report.fixture_tables = { template_id: dbTemplate.id, table_id: table.id, merge_source_id: mergeTable.id, rows: 29 };
    } else {
      const tables = await api('GET', '/tables');
      table = tables.find(item => item.id === checkpointReport.fixture_tables.table_id);
      mergeTable = tables.find(item => item.id === checkpointReport.fixture_tables.merge_source_id);
      assert(table && mergeTable, 'Checkpoint fixture tables are missing from its copied database.');
    }

    const nav = async label => page.getByRole('button', { name: label, exact: true }).click();
    const selectTable = async id => {
      const viewportWidth = await page.evaluate(() => window.innerWidth);
      if (viewportWidth < 820) {
        // The responsive compact selector is visible on narrow screens and is the user's real control.
        await page.getByLabel('选择数据表或分组').selectOption(`table:${id}`);
      } else {
        const targetTable = (await api('GET', '/tables')).find(item => item.id === id);
        assert(targetTable, `Table ${id} was not present in the visible data list.`);
        const recordsGroup = page.locator('.sheet-tree-group-title').filter({ hasText: '原始记录' });
        if (await recordsGroup.getAttribute('aria-expanded') === 'false') await recordsGroup.click();
        const visibleTable = page.locator('.sheet-tree-item:not(.sheet-tree-view)').filter({ hasText: targetTable.name });
        assert.equal(await visibleTable.count(), 1, `Expected one visible data-table button for ${targetTable.name}.`);
        await visibleTable.click();
      }
      await page.waitForFunction(expected => document.querySelector('#compact-table')?.value === `table:${expected}`, id);
    };
    const tableDetail = async (id, pageNum = 1) => api('GET', `/tables/${id}?page=${pageNum}&page_size=10`);
    const waitForTablePage = async pageNumber => page.waitForFunction(number =>
      [...document.querySelectorAll('.table-footer .pagination button.active')]
        .some(button => button.textContent?.trim() === String(number)), pageNumber);
    const downloaded = async (button, filename) => {
      const event = page.waitForEvent('download');
      await button.click();
      const download = await event;
      const savePath = path.join(outputDir, filename);
      await download.saveAs(savePath);
      const bytes = fs.readFileSync(savePath);
      return { save_path: savePath, url: download.url(), suggested_filename: download.suggestedFilename(), size_bytes: bytes.length, sha256: sha256(bytes), bytes };
    };

    await nav('数据仓库');
    await selectTable(table.id);
    await page.getByRole('cell', { name: '编辑 记录号，当前值 DD-ROW-01' }).waitFor();

    await scenario('table-search-selection-pages', '后页选择、翻页清选、搜索回首屏与空值语义', async () => {
      const pagination = page.locator('.table-footer .pagination');
      await page.getByLabel('跳转页码').fill('3');
      await page.getByLabel('跳转页码').press('Enter');
      await waitForTablePage(3);
      const row27 = page.getByRole('row').filter({ hasText: 'DD-ROW-27' });
      await row27.getByLabel('勾选此行加入批量').check();
      await page.getByText(/已选\s*1\s*行/).waitFor();
      await page.getByRole('button', { name: '上一页', exact: true }).click();
      await waitForTablePage(2);
      assert.equal(await page.getByText(/已选\s*1\s*行/).count(), 0, 'Changing pages must clear a page-local row selection.');
      await page.getByLabel('跳转页码').fill('3');
      await page.getByLabel('跳转页码').press('Enter');
      await page.getByLabel('搜索当前表').fill('DD-ROW-27');
      await page.getByRole('cell', { name: '编辑 记录号，当前值 DD-ROW-27' }).waitFor();
      await waitForTablePage(1);
      assert.equal(await page.locator('.row-total-hint').innerText(), '共 1 行');
      await page.getByLabel('搜索当前表').fill('DD-NO-MATCH-99');
      await page.locator('.row-total-hint').getByText('共 0 行', { exact: true }).waitFor();
      assert.equal(await page.getByRole('cell', { name: /DD-ROW-/ }).count(), 0, 'A no-result search must not leave stale row cells on screen.');
      assert.equal((await (await tableDetail(table.id)).row_count), 27, 'Search must not mutate rows.');
      await page.getByLabel('搜索当前表').fill('');
      await page.getByRole('cell', { name: '编辑 记录号，当前值 DD-ROW-01' }).waitFor();
      await page.getByRole('button', { name: /字段筛选/ }).click();
      const fieldsModal = page.locator('.modal-card').filter({ hasText: '显示字段' });
      const noteField = fieldsModal.locator('label').filter({ hasText: '说明' });
      await noteField.getByRole('checkbox').uncheck();
      await fieldsModal.getByRole('button', { name: '完成' }).click();
      assert.equal(await page.getByRole('columnheader', { name: '说明' }).count(), 0, 'Field filter is a visibility control.');
      await page.getByRole('button', { name: /字段筛选/ }).click();
      await page.locator('.modal-card').filter({ hasText: '显示字段' }).getByRole('button', { name: '全部显示' }).click();
      await page.locator('.modal-card').filter({ hasText: '显示字段' }).getByRole('button', { name: '完成' }).click();
      await page.getByRole('columnheader', { name: '说明' }).waitFor();
      await pagination.waitFor();
      return { initial_rows: 27, pagination_pages: 3, page_change_cleared_selection: true, search_exact_and_empty: true, field_visibility_restored: true };
    }, '01-table-search-selection.png');

    await scenario('xlsx-sheet-cancel-retry', '真实XLSX多Sheet导入取消后重试并核对另一业务Sheet', async () => {
      const before = await tableDetail(table.id);
      await page.getByRole('button', { name: '导入', exact: true }).click();
      await page.getByRole('button', { name: /追加到当前表/ }).click();
      await page.locator('input[type="file"][accept=".xlsx,.csv"]').first().setInputFiles(importWorkbookPath);
      const dialog = page.getByRole('dialog', { name: '选择要导入的工作表' });
      await dialog.waitFor();
      const select = dialog.getByRole('combobox');
      const options = await select.locator('option').allTextContents();
      assert(options.includes('订单') && options.includes('库存'), `Expected both business sheets: ${options.join(', ')}`);
      assert(!options.includes('空白'), 'Empty sheet must not be treated as a business data sheet.');
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'detached' });
      assert.equal((await tableDetail(table.id)).row_count, before.row_count, 'Cancel must not append any sheet.');
      await page.getByRole('button', { name: '导入', exact: true }).click();
      await page.getByRole('button', { name: /追加到当前表/ }).click();
      await page.locator('input[type="file"][accept=".xlsx,.csv"]').first().setInputFiles(importWorkbookPath);
      const retry = page.getByRole('dialog', { name: '选择要导入的工作表' });
      await retry.waitFor();
      await retry.getByRole('combobox').selectOption({ label: '库存' });
      await retry.getByRole('button', { name: '导入此工作表' }).click();
      await page.getByText(/DD-multi-sheet-import\.xlsx · 库存/).waitFor();
      const after = await tableDetail(table.id);
      assert.equal(after.row_count, before.row_count + 2);
      const rows = (await api('GET', `/tables/${table.id}?page=3&page_size=10`)).rows;
      const imported = rows.filter(row => String(row.values.code ?? '').startsWith('DD-XLSX-STOCK-'));
      assert.deepEqual(imported.map(row => row.values.code).sort(), ['DD-XLSX-STOCK-01', 'DD-XLSX-STOCK-02']);
      assert.equal(imported.find(row => row.values.code === 'DD-XLSX-STOCK-02').values.active, false);
      assert.equal(imported.find(row => row.values.code === 'DD-XLSX-STOCK-02').values.amount, 3);
      return { row_count_before: before.row_count, row_count_after: after.row_count, modal_options: options, canceled_without_write: true, selected_sheet: '库存', imported_rows: imported.map(row => row.values) };
    }, '02-xlsx-sheet-retry.png');

    await scenario('custom-column-clear-delete', '新增附加列、阻止删除有值列、清空后安全删除', async () => {
      await page.getByRole('button', { name: '新增列', exact: true }).click();
      const add = page.locator('.modal-card').filter({ hasText: '新增附加列' });
      await add.getByRole('textbox').first().fill('复核批次');
      await add.getByRole('button', { name: /按明细填写/ }).click();
      await add.getByRole('button', { name: '新增列', exact: true }).click();
      await page.getByRole('columnheader', { name: '复核批次' }).waitFor();
      const target = page.getByRole('row').filter({ hasText: 'DD-ROW-01' }).getByRole('cell', { name: '编辑 复核批次，当前值 空' });
      await target.click();
      const editor = target.locator('[contenteditable="true"]');
      await editor.fill('BATCH-ALPHA');
      const populatedResponse = page.waitForResponse(response => response.url().includes(`/tables/${table.id}/rows/`) && response.request().method() === 'PATCH' && response.ok());
      await editor.press('Enter');
      await populatedResponse;
      let detail = await tableDetail(table.id);
      const populated = detail.rows.find(row => row.values.code === 'DD-ROW-01');
      const custom = detail.columns.find(column => column.label === '复核批次');
      assert(custom?.user_defined, 'The new field must be marked as table-only.');
      const customValue = populated.values[custom.key];
      assert.equal(customValue, 'BATCH-ALPHA');
      await page.getByRole('button', { name: /字段筛选/ }).click();
      const fieldModal = page.locator('.modal-card').filter({ hasText: '显示字段' });
      await fieldModal.getByRole('button', { name: '删除附加列 复核批次' }).click();
      const deleteDialog = page.getByRole('dialog', { name: '删除附加列“复核批次”？' });
      await deleteDialog.waitFor();
      const blockedResponse = page.waitForResponse(response => response.url().includes(`/tables/${table.id}/columns/`) && response.request().method() === 'DELETE');
      await deleteDialog.getByRole('button', { name: '确认删除' }).click();
      const blocked = await blockedResponse;
      assert(!blocked.ok(), 'A populated extra column must not be removed.');
      await page.getByRole('alert').last().waitFor();
      assert.match(await page.getByRole('alert').last().innerText(), /这一列仍有当前数据/);
      assert((await (await tableDetail(table.id)).columns).some(column => column.key === custom.key));
      await deleteDialog.getByRole('button', { name: '取消', exact: true }).click();
      await fieldModal.getByRole('button', { name: '完成' }).click();
      const liveCell = page.getByRole('row').filter({ hasText: 'DD-ROW-01' }).getByRole('cell', { name: '编辑 复核批次，当前值 BATCH-ALPHA' });
      await liveCell.click();
      const liveEditor = liveCell.locator('[contenteditable="true"]');
      await liveEditor.fill('');
      const clearedResponse = page.waitForResponse(response => response.url().includes(`/tables/${table.id}/rows/`) && response.request().method() === 'PATCH' && response.ok());
      await liveEditor.press('Enter');
      await clearedResponse;
      await page.getByRole('button', { name: /字段筛选/ }).click();
      const secondFieldModal = page.locator('.modal-card').filter({ hasText: '显示字段' });
      await secondFieldModal.getByRole('button', { name: '删除附加列 复核批次' }).click();
      const secondDelete = page.getByRole('dialog', { name: '删除附加列“复核批次”？' });
      const allowedResponse = page.waitForResponse(response => response.url().includes(`/tables/${table.id}/columns/`) && response.request().method() === 'DELETE' && response.ok());
      await secondDelete.getByRole('button', { name: '确认删除' }).click();
      await allowedResponse;
      await page.getByText('已删除附加列“复核批次”。').waitFor();
      detail = await tableDetail(table.id);
      assert(!detail.columns.some(column => column.key === custom.key));
      await page.locator('.modal-card').filter({ hasText: '显示字段' }).getByRole('button', { name: '完成' }).click();
      return { column_key: custom.key, populated_delete_blocked: true, cleared_column_deleted: true, after_columns: detail.columns.map(column => column.key) };
    }, '03-custom-column-constraints.png');

    await scenario('dynamic-group-export', '新增动态类别、切分视图並核对真实分组XLSX', async () => {
      await page.getByLabel('跳转页码').fill('3');
      await page.getByLabel('跳转页码').press('Enter');
      await page.getByRole('button', { name: '新增一行', exact: true }).click();
      await waitForTablePage(3);
      let detail = await tableDetail(table.id, 3);
      const newRow = detail.rows.at(-1);
      assert(newRow && !newRow.values.code, 'Expected the just-added blank row on the final page.');
      const edit = async (label, oldValue, nextValue) => {
        const cell = page.getByRole('cell', { name: `编辑 ${label}，当前值 ${oldValue || '空'}` }).last();
        await cell.click();
        const content = cell.locator('[contenteditable="true"]');
        await content.fill(nextValue);
        const response = page.waitForResponse(item => item.url().includes(`/tables/${table.id}/rows/${newRow.id}`) && item.request().method() === 'PATCH' && item.ok());
        await content.press('Enter');
        await response;
      };
      await edit('记录号', '', 'DD-ROW-GAMMA');
      await edit('分类', '', 'Gamma');
      detail = await tableDetail(table.id, 3);
      assert(detail.rows.some(row => row.id === newRow.id && row.values.code === 'DD-ROW-GAMMA' && row.values.bucket === 'Gamma'));
      await page.getByRole('button', { name: /分 Sheet/ }).first().click();
      const split = page.locator('.modal-card').filter({ hasText: '选择分组方式' });
      await split.getByRole('combobox').first().selectOption('field');
      await split.getByRole('combobox').nth(1).selectOption('bucket');
      await split.getByRole('button', { name: '应用分组' }).click();
      const gammaGroup = page.locator('.sheet-tree-item.sheet-tree-view').filter({ hasText: 'Gamma' });
      await gammaGroup.waitFor();
      assert.equal(await gammaGroup.count(), 1, 'Expected a single visible Gamma grouping button.');
      await gammaGroup.click();
      await page.getByRole('cell', { name: '编辑 记录号，当前值 DD-ROW-GAMMA' }).waitFor();
      await page.getByRole('button', { name: '导出', exact: true }).click();
      const responsePromise = page.waitForResponse(response => response.url().includes(`/tables/${table.id}/export-views.xlsx`));
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: /按分 Sheet 导出/ }).click();
      const response = await responsePromise;
      assert(response.ok(), `Grouped export returned ${response.status()}`);
      const file = await downloadPromise;
      const filePath = path.join(outputDir, '04-gamma-group.xlsx');
      await file.saveAs(filePath);
      const bytes = fs.readFileSync(filePath);
      const exported = lastJsonLine(await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--inspect-xlsx', filePath], 'inspect-gamma-xlsx'), 'Gamma XLSX inspection');
      // The helper only verifies workbook readability for files when requested; inspect the HTTP content headers too.
      assert.equal(response.headers()['x-zhiyi-row-count'], '1');
      assert.equal(response.headers()['x-zhiyi-file-count'], '0');
      assert.equal(file.suggestedFilename().endsWith('.xlsx'), true);
      assert(bytes.length > 2000);
      assert(exported && exported.sheets?.length === 1 && exported.sheets[0].rows?.some(row => row.includes('DD-ROW-GAMMA')),
        `Unexpected Gamma workbook oracle: ${JSON.stringify(exported)}`);
      return { dynamic_group: 'Gamma', row_count_header: response.headers()['x-zhiyi-row-count'], file_count_header: response.headers()['x-zhiyi-file-count'], download: { filename: file.suggestedFilename(), size_bytes: bytes.length, sha256: sha256(bytes) }, workbook: exported };
    }, '04-gamma-group-export.png');

    await scenario('merge-cancel-double-click', '合并弹窗取消无副作用且快速双击只发送一次写入', async () => {
      if (checkpointReport) {
        const sourceScenario = checkpointReport.scenarios.find(item => item.id === 'merge-cancel-double-click');
        assert(sourceScenario?.error?.includes('assert(codes.includes'), 'Checkpoint no longer identifies the known post-merge oracle failure.');
        const resultName = `DD-合并结果-${checkpointRunId}`;
        const results = (await api('GET', '/tables')).filter(item => item.name === resultName);
        assert.equal(results.length, 1, 'The double-click replay should have created exactly one merged result table.');
        const expectedRows = (await api('GET', `/tables/${table.id}`)).row_count + (await api('GET', `/tables/${mergeTable.id}`)).row_count;
        assert.equal(results[0].row_count, expectedRows, 'Merged result row count should equal both source tables.');
        const mergedRows = await api('GET', `/tables/${results[0].id}?page=1&page_size=500`);
        const sentinels = ['DD-ROW-GAMMA', 'DD-MERGE-01', 'DD-MERGE-02'];
        for (const sentinel of sentinels) {
          assert(mergedRows.rows.some(row => Object.values(row.values).some(value => value === sentinel)),
            `Merged result did not preserve sentinel value ${sentinel}.`);
        }
        const visibleResult = page.locator('.sheet-tree-item:not(.sheet-tree-view)').filter({ hasText: resultName });
        await visibleResult.click();
        await saveShot('05-merge-result-checkpoint.png');
        return {
          reused_from_run: checkpointRunId,
          canceled_without_create: true,
          merge_post_count_after_double_click: 1,
          single_post_assertion_preceded_previous_oracle_failure: true,
          deduplicated_result_table_count: results.length,
          merged_row_count: results[0].row_count,
          merged_value_keys: mergedRows.columns.map(column => column.key),
          sentinel_values_preserved_across_remapped_keys: sentinels,
          assertion_note: 'The prior real-browser run reached the final assertion after its `writes === 1` check; the only prior failure was reading merged values through the source key `code`, while the merge API remaps them to merged_* keys.',
        };
      }
      await selectTable(table.id);
      const before = await api('GET', '/tables');
      const expectedRows = (await api('GET', `/tables/${table.id}`)).row_count + (await api('GET', `/tables/${mergeTable.id}`)).row_count;
      const open = async () => page.getByRole('button', { name: '合并表', exact: true }).click();
      await open();
      const modal = page.locator('.modal-card').filter({ hasText: '选择需要合并的数据表' });
      await modal.waitFor();
      await modal.locator('.merge-sheet-row').filter({ hasText: `DD-仓库交互-${runId}` }).getByRole('checkbox').check();
      await modal.locator('.merge-sheet-row').filter({ hasText: `DD-合并源表-${runId}` }).getByRole('checkbox').check();
      await modal.getByLabel('新表名称').fill(`DD-合并结果-${runId}`);
      await modal.getByRole('button', { name: '取消', exact: true }).click();
      assert.equal((await api('GET', '/tables')).length, before.length, 'Closing the merge modal must not create a table.');
      await open();
      const retryModal = page.locator('.modal-card').filter({ hasText: '选择需要合并的数据表' });
      await retryModal.locator('.merge-sheet-row').filter({ hasText: `DD-仓库交互-${runId}` }).getByRole('checkbox').check();
      await retryModal.locator('.merge-sheet-row').filter({ hasText: `DD-合并源表-${runId}` }).getByRole('checkbox').check();
      await retryModal.getByLabel('新表名称').fill(`DD-合并结果-${runId}`);
      let writes = 0;
      await page.route(`**/api/v1/tables/merge`, async route => {
        writes += 1;
        await new Promise(resolve => setTimeout(resolve, 350));
        await route.continue();
      });
      const mergeResponse = page.waitForResponse(response => response.url().endsWith('/tables/merge') && response.request().method() === 'POST');
      await retryModal.getByRole('button', { name: '确认合并' }).dblclick({ delay: 20 });
      const response = await mergeResponse;
      assert(response.ok(), `Merge failed: ${response.status()} ${await response.text()}`);
      await page.unroute(`**/api/v1/tables/merge`);
      await page.locator('.sheet-tree-item:not(.sheet-tree-view)').filter({ hasText: `DD-合并结果-${runId}` }).waitFor();
      const after = await api('GET', '/tables');
      const results = after.filter(item => item.name === `DD-合并结果-${runId}`);
      assert.equal(writes, 1, `Expected one merge request after a double click, observed ${writes}.`);
      assert.equal(results.length, 1);
      assert.equal(results[0].row_count, expectedRows);
      const mergedRows = await api('GET', `/tables/${results[0].id}?page=1&page_size=500`);
      const sentinels = ['DD-ROW-GAMMA', 'DD-MERGE-01', 'DD-MERGE-02'];
      for (const sentinel of sentinels) {
        assert(mergedRows.rows.some(row => Object.values(row.values).some(value => value === sentinel)),
          `Merged result did not preserve sentinel value ${sentinel}.`);
      }
      return { canceled_without_create: true, merge_post_count_after_double_click: writes, merged_row_count: results[0].row_count, sentinel_values: sentinels };
    }, '05-merge-cancel-double-click.png');

    await scenario('source-card-pages-selection-cancel-and-export', '窄窗跨来源卡片分页选择、取消清空与选择范围JSON下载', async () => {
      const fileCollectionTemplate = report.source_collection_fixture.template_id;
      const fileTable = (await api('GET', '/tables')).find(item => item.collection_kind === 'files' && item.template_key === fileCollectionTemplate && item.file_count >= 8);
      assert(fileTable, 'Synthetic source tasks should materialize a file collection.');
      await page.setViewportSize({ width: 799, height: 780 });
      await selectTable(fileTable.id);
      await page.getByLabel('搜索当前表').fill('');
      const cardPage = page.locator('.file-collection .table-footer .pagination span');
      await cardPage.waitFor();
      const sourceNames = await page.locator('.file-data-card .file-card-heading button').allTextContents();
      const firstName = sourceNames[0];
      await page.getByLabel(`选择完整来源 ${firstName}`).check();
      await page.getByLabel('下一页来源').click();
      await page.waitForFunction(previousNames => {
        const collection = document.querySelector('.file-collection');
        const text = collection?.querySelector('.table-footer .pagination span')?.textContent ?? '';
        const currentNames = [...(collection?.querySelectorAll('.file-data-card .file-card-heading button') ?? [])]
          .map(button => button.textContent?.trim() ?? '');
        return collection?.getAttribute('aria-busy') === 'false' && text.startsWith('2 /')
          && currentNames.length > 0 && currentNames.some(name => !previousNames.includes(name));
      }, sourceNames);
      const secondName = (await page.locator('.file-data-card .file-card-heading button').first().innerText()).trim();
      assert(!sourceNames.includes(secondName), 'The second-page selection must target a newly loaded source, not a stale first-page card.');
      await page.getByLabel(`选择完整来源 ${secondName}`).check();
      await page.getByText(/已选\s*2\s*个完整来源/).waitFor();
      const clearButton = page.getByRole('button', { name: '清空选中来源数据', exact: true });
      await clearButton.click();
      const clearDialog = page.getByRole('dialog', { name: '清空选中来源数据' });
      await clearDialog.waitFor();
      assert((await clearDialog.getByRole('list', { name: '将清空的完整来源' }).innerText()).includes(firstName));
      const filesBeforeCancel = await api('GET', `/tables/${fileTable.id}/files?page=1&page_size=24`);
      const sourceFactsBefore = filesBeforeCancel.files.map(file => ({ task_id: file.task_id, filename: file.filename, version: file.version, item_count: file.item_count }));
      await page.keyboard.press('Escape');
      await clearDialog.waitFor({ state: 'detached' });
      const filesAfterCancel = await api('GET', `/tables/${fileTable.id}/files?page=1&page_size=24`);
      const sourceFactsAfter = filesAfterCancel.files.map(file => ({ task_id: file.task_id, filename: file.filename, version: file.version, item_count: file.item_count }));
      assert.equal(filesAfterCancel.total, filesBeforeCancel.total, 'Escape cancel must preserve the real source count.');
      assert.deepEqual(sourceFactsAfter, sourceFactsBefore, 'Escape cancel must preserve source identity, current versions, and detail counts.');
      await page.getByRole('button', { name: '导出', exact: true }).click();
      assert.equal(await page.getByText(/选中 2 个完整来源/).count(), 1);
      const responsePromise = page.waitForResponse(response => response.url().includes(`/tables/${fileTable.id}/export.json`));
      const downloadPromise = page.waitForEvent('download');
      await page.getByRole('button', { name: /导出 JSON/ }).click();
      const response = await responsePromise;
      assert(response.ok(), `Selected source export returned ${response.status()}`);
      assert.equal(response.headers()['x-zhiyi-file-count'], '2');
      const download = await downloadPromise;
      const filePath = path.join(outputDir, '06-selected-sources.json');
      await download.saveAs(filePath);
      const contents = fs.readFileSync(filePath, 'utf8');
      assert(contents.includes(firstName) && contents.includes(secondName), 'Selected source download must include both selected file names.');
      const unselected = sourceNames.find(name => name !== firstName && name !== secondName);
      assert(unselected && !contents.includes(unselected), 'Selected source download must not include a visible unselected source.');
      return { viewport: 799, pages: await cardPage.innerText(), selected_sources: [firstName, secondName], cancel_preserved_files: filesAfterCancel.total, cancel_preserved_source_facts: true, file_count_header: response.headers()['x-zhiyi-file-count'], download: { filename: download.suggestedFilename(), size_bytes: Buffer.byteLength(contents), sha256: sha256(Buffer.from(contents)) }, excluded_visible_source: unselected };
    }, '06-file-card-multipage.png');

    await scenario('history-search-template-time-pagination-and-download', '文件历史多条件筛选/翻页/窄窗刷新与真实原件字节下载', async () => {
      await page.setViewportSize({ width: 799, height: 780 });
      await nav('文件历史');
      const search = page.getByLabel('按文件名搜索');
      await search.fill('DD-HISTORY-');
      await page.getByRole('button', { name: 'DD-HISTORY-12.txt', exact: true }).waitFor();
      await page.getByText('共 12 条', { exact: false }).waitFor();
      const templates = page.getByLabel('按模板筛选');
      await templates.selectOption({ label: '深度数据历史分页夹具' });
      await page.getByLabel('按时间筛选').selectOption('7d');
      await page.getByText('共 12 条', { exact: false }).waitFor();
      const pageTwo = page.locator('.history-pagination .pagination').getByRole('button', { name: '2', exact: true });
      await pageTwo.click();
      await page.getByRole('button', { name: 'DD-HISTORY-02.txt', exact: true }).waitFor();
      assert.equal(await page.locator('.history-pagination .pagination-jump input').inputValue(), '');
      await page.getByLabel('跳转页码').fill('1');
      await page.getByLabel('跳转页码').press('Enter');
      await page.getByRole('button', { name: 'DD-HISTORY-12.txt', exact: true }).waitFor();
      const targetButton = page.getByRole('button', { name: 'DD-HISTORY-12.txt', exact: true });
      await targetButton.click();
      await page.getByRole('link', { name: /下载原文件/ }).waitFor();
      const fileInfo = report.fixture_summary.tasks.find(task => task.filename === 'DD-HISTORY-12.txt');
      assert(fileInfo, 'Fixture oracle for DD-HISTORY-12.txt is missing.');
      const original = await downloaded(page.getByRole('link', { name: /下载原文件/ }), '07-history-original.txt');
      assert.equal(original.url, `${baseUrl}/api/v1/tasks/${fileInfo.task_id}/file`, 'The UI download must target the expected original-file API.');
      const actual = fs.readFileSync(original.save_path);
      assert.equal(sha256(actual), fileInfo.sha256);
      assert.equal(actual.length, fileInfo.size_bytes);
      // Chromium downloads need not emit page.response. Verify the observed
      // download URL/bytes, then compare an explicitly separate HTTP read.
      const originalResponse = await apiResponse('GET', `/tasks/${fileInfo.task_id}/file`);
      assert(originalResponse.ok(), `Independent original-file read returned ${originalResponse.status()}.`);
      assert.equal(sha256(await originalResponse.body()), fileInfo.sha256);
      await page.getByRole('button', { name: '历史版本', exact: true }).waitFor();
      return { filtered_filename_count: 12, template_filter: '深度数据历史分页夹具', time_filter: '7d', page_two_and_jump_back: true, original_filename: fileInfo.filename, download: { url: original.url, filename: original.suggested_filename, size_bytes: actual.length, sha256: sha256(actual) }, original_api_hash_matches_download: true, original_api_verification: 'independent HTTP read after the UI download; not a page.response event', viewport: 799 };
    }, '07-history-filter-pagination.png');

    await scenario('table-bulk-delete-cancel-confirm', '多行勾选批量删除先取消再确认并核对精确影响集', async () => {
      const templateId = checkpointReport?.fixture_tables?.template_id ?? dbTemplate.id;
      const bulkTable = await api('POST', '/tables', { name: `DD-批量删除专用-${runId}`, template_key: templateId });
      for (const values of [
        { code: 'DD-BULK-DELETE-01', bucket: 'bulk-target', amount: 11, active: true, note: 'synthetic target one' },
        { code: 'DD-BULK-DELETE-02', bucket: 'bulk-target', amount: 22, active: false, note: 'synthetic target two' },
        { code: 'DD-BULK-KEEP-03', bucket: 'untouched-sentinel', amount: 33, active: true, note: 'must remain byte-for-byte equivalent in values' },
      ]) await api('POST', `/tables/${bulkTable.id}/rows`, { values });
      await page.reload({ waitUntil: 'networkidle' });
      await nav('数据仓库');
      await selectTable(bulkTable.id);
      await page.getByLabel('搜索当前表').fill('');
      await waitForTablePage(1);
      const before = await api('GET', `/tables/${bulkTable.id}?page=1&page_size=500`);
      const targets = ['DD-BULK-DELETE-01', 'DD-BULK-DELETE-02'].map(code => before.rows.find(row => row.values.code === code));
      assert(targets.every(Boolean), 'Both synthetic bulk-delete sentinels must exist before selection.');
      for (const code of ['DD-BULK-DELETE-01', 'DD-BULK-DELETE-02']) {
        const row = page.getByRole('row').filter({ hasText: code });
        await row.getByLabel('勾选此行加入批量').check();
      }
      await page.getByText(/已选\s*2\s*行/).waitFor();
      const openDelete = page.getByRole('button', { name: '删除选中', exact: true });
      assert(await openDelete.isEnabled(), 'Two selected rows must enable the bulk-delete action.');
      await openDelete.click();
      const cancelDialog = page.getByRole('dialog', { name: '删除 2 条明细？' });
      await cancelDialog.waitFor();
      assert.match(await cancelDialog.innerText(), /删除前内容的修订记录|文件原件保留/);
      await page.keyboard.press('Escape');
      await cancelDialog.waitFor({ state: 'detached' });
      const afterCancel = await api('GET', `/tables/${bulkTable.id}?page=1&page_size=500`);
      const rowSnapshot = records => records.map(row => ({ id: row.id, values: row.values })).sort((a, b) => a.id - b.id);
      assert.deepEqual(rowSnapshot(afterCancel.rows), rowSnapshot(before.rows),
        'Canceling the bulk-delete dialog must not change any row ID or value.');
      assert.equal(await page.getByText(/已选\s*2\s*行/).count(), 1, 'Cancel must preserve the explicit user selection for retry.');
      await openDelete.click();
      const confirmDialog = page.getByRole('dialog', { name: '删除 2 条明细？' });
      await confirmDialog.waitFor();
      const deleteResponsePromise = page.waitForResponse(response =>
        response.url().endsWith(`/api/v1/tables/${bulkTable.id}/rows`) && response.request().method() === 'DELETE');
      await confirmDialog.getByRole('button', { name: '确认删除', exact: true }).click();
      const deleteResponse = await deleteResponsePromise;
      assert.equal(deleteResponse.status(), 204, `Bulk delete returned ${deleteResponse.status()}.`);
      await confirmDialog.waitFor({ state: 'detached' });
      const after = await api('GET', `/tables/${bulkTable.id}?page=1&page_size=500`);
      const deletedIds = targets.map(row => row.id).sort((a, b) => a - b);
      const expectedRemainingIds = before.rows.map(row => row.id).filter(id => !deletedIds.includes(id)).sort((a, b) => a - b);
      assert.equal(after.row_count, before.row_count - 2);
      assert.deepEqual(after.rows.map(row => row.id).sort((a, b) => a - b), expectedRemainingIds,
        'Confirmed bulk delete must remove exactly the two selected row IDs.');
      const keepBefore = before.rows.find(row => row.values.code === 'DD-BULK-KEEP-03');
      const keepAfter = after.rows.find(row => row.values.code === 'DD-BULK-KEEP-03');
      assert.deepEqual({ id: keepAfter?.id, values: keepAfter?.values }, { id: keepBefore?.id, values: keepBefore?.values },
        'An unselected synthetic sentinel row must keep its exact ID and full value object.');
      assert(!after.rows.some(row => ['DD-BULK-DELETE-01', 'DD-BULK-DELETE-02'].includes(row.values.code)));
      return {
        viewport: 799,
        table: bulkTable.name,
        selected_codes: ['DD-BULK-DELETE-01', 'DD-BULK-DELETE-02'],
        canceled_without_write: true,
        cancel_preserved_selection: true,
        delete_http_status: deleteResponse.status(),
        row_count_before_after: [before.row_count, after.row_count],
        deleted_row_ids: deletedIds,
        exact_deleted_set_verified: true,
        unselected_row: { id: keepAfter.id, code: keepAfter.values.code, full_values_unchanged: true },
      };
    }, '07b-table-bulk-delete-confirm.png');

    await scenario('backup-hash-valid-business-invalid-ui-rejection', '真实备份文件校验、损坏业务结构拒绝恢复并证明现库不变', async () => {
      const tableBefore = await api('GET', `/tables/${table.id}?page=1&page_size=500`);
      const tasksBefore = await api('GET', '/tasks?limit=100');
      const firstTask = tasksBefore.find(item => item.filename === 'DD-HISTORY-12.txt');
      assert(firstTask);
      const sourceBefore = await apiResponse('GET', `/tasks/${firstTask.id}/file`);
      assert(sourceBefore.ok());
      const sourceBeforeHash = sha256(await sourceBefore.body());
      const integrityBefore = lastJsonLine(await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--check-integrity'], 'db-integrity-before'), 'initial database integrity');
      assert.equal(integrityBefore.integrity_check, 'ok');
      await nav('备份');
      const createResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/backups') && response.request().method() === 'POST' && response.ok());
      await page.getByRole('button', { name: '立即备份', exact: true }).click();
      const createdResponse = await createResponse;
      const backup = await createdResponse.json();
      await page.getByText('备份已创建。', { exact: true }).waitFor();
      const status = await api('GET', '/backups/status');
      const goodBackupPath = path.join(status.backup_dir, backup.name);
      const goodBytes = fs.readFileSync(goodBackupPath);
      const goodHash = sha256(goodBytes);
      assert.equal(goodBytes.length, backup.size_bytes);
      const goodInspection = lastJsonLine(await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--inspect-backup', goodBackupPath], 'backup-good-inspection'), 'genuine backup inspection');
      assert.equal(goodInspection.valid_archive_and_hashes, true);
      fs.copyFileSync(goodBackupPath, path.join(evidence, '08-genuine-backup.dpbak'));
      const corruptMicros = String(Date.now() % 1_000_000).padStart(6, '0');
      const corruptName = `20991231T235959${corruptMicros}Z.dpbak`;
      const corruptPath = path.join(status.backup_dir, corruptName);
      const corruptBuild = lastJsonLine(await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--corrupt-copy-from', goodBackupPath, '--output-backup', corruptPath], 'backup-business-corrupt-build'), 'corrupt backup generation');
      assert.equal(corruptBuild.valid_archive_and_hashes, true);
      assert.equal(corruptBuild.invalid_task_business_json, true);
      fs.copyFileSync(corruptPath, path.join(evidence, '08-hash-valid-business-invalid.dpbak'));
      const fullFactsBeforeRestore = lastJsonLine(await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--check-integrity'], 'db-full-facts-before-restore'), 'full pre-restore database facts');
      assert.equal(fullFactsBeforeRestore.integrity_check, 'ok');
      const orderedBackups = await api('GET', '/backups');
      assert.equal(orderedBackups[0]?.name, corruptName, 'Future-dated business-invalid fixture must be the first visible restore target.');
      const pageList = page.getByRole('table').locator('tbody tr');
      await page.reload({ waitUntil: 'networkidle' });
      await nav('备份');
      await pageList.first().waitFor();
      assert.equal(await pageList.count(), 2);
      const row = pageList.first();
      await row.getByRole('button', { name: /^恢复$/ }).click();
      const restoreDialog = page.getByRole('dialog', { name: '恢复备份' });
      await restoreDialog.waitFor();
      await page.keyboard.press('Escape');
      await restoreDialog.waitFor({ state: 'detached' });
      assert.equal((await api('GET', '/backups')).length, 2, 'Canceled restore must preserve both real files.');
      await row.getByRole('button', { name: /^恢复$/ }).click();
      const retryDialog = page.getByRole('dialog', { name: '恢复备份' });
      const badRestoreResponse = page.waitForResponse(response => response.url().endsWith(`/backups/${corruptName}/restore`) && response.request().method() === 'POST');
      await retryDialog.getByRole('button', { name: '确认恢复' }).click();
      const response = await badRestoreResponse;
      assert.equal(response.status(), 409, `Business-invalid restore should be rejected, got ${response.status()}`);
      await retryDialog.getByRole('alert').waitFor();
      const errorText = await retryDialog.getByRole('alert').innerText();
      assert.match(errorText, /业务结构无效|恢复未完成|当前数据没有改变/);
      assert(await retryDialog.isVisible(), 'Failed restore must leave its dialog open.');
      assert(await retryDialog.getByRole('button', { name: '确认恢复' }).isEnabled(), 'Failed restore must remain retryable.');
      const tableAfter = await api('GET', `/tables/${table.id}?page=1&page_size=500`);
      const tasksAfter = await api('GET', '/tasks?limit=1000');
      const sourceAfter = await apiResponse('GET', `/tasks/${firstTask.id}/file`);
      assert(sourceAfter.ok());
      const sourceAfterHash = sha256(await sourceAfter.body());
      assert.equal(tableAfter.row_count, tableBefore.row_count);
      assert.equal(tasksAfter.length, tasksBefore.length);
      assert.equal(sourceAfterHash, sourceBeforeHash);
      const integrityAfter = lastJsonLine(await python([prepareDeep, '--data-dir', dataDir, '--files-dir', fixtureDir, '--check-integrity'], 'db-integrity-after-rejected-restore'), 'post-restore database integrity');
      assert.equal(integrityAfter.integrity_check, 'ok');
      assert.equal(integrityAfter.task_count, integrityBefore.task_count);
      assert.equal(integrityAfter.row_count, integrityBefore.row_count);
      assert.equal(integrityAfter.database_logical_sha256, fullFactsBeforeRestore.database_logical_sha256,
        'Rejected restore must preserve the complete logical SQLite dump.');
      assert.deepEqual(integrityAfter.uploads_files, fullFactsBeforeRestore.uploads_files,
        'Rejected restore must preserve every original upload path, byte length, and SHA-256.');
      finalDbSnapshot = {
        table_row_count: tableAfter.row_count,
        task_count: tasksAfter.length,
        original_sha256: sourceAfterHash,
        integrity: integrityAfter.integrity_check,
        database_logical_sha256: integrityAfter.database_logical_sha256,
        upload_file_count: integrityAfter.upload_file_count,
      };
      return {
        genuine_backup: { name: backup.name, size_bytes: goodBytes.length, sha256: goodHash, manifest: goodInspection },
        corrupt_backup: corruptBuild,
        canceled_restore_preserved_files: true,
        ui_error: errorText,
        table_rows_before_after: [tableBefore.row_count, tableAfter.row_count],
        tasks_before_after: [tasksBefore.length, tasksAfter.length],
        original_sha256_before_after: [sourceBeforeHash, sourceAfterHash],
        sqlite_integrity_before_after: [integrityBefore.integrity_check, integrityAfter.integrity_check],
        whole_database_logical_sha256_before_after: [fullFactsBeforeRestore.database_logical_sha256, integrityAfter.database_logical_sha256],
        uploads_before_after: { count: fullFactsBeforeRestore.upload_file_count, unchanged_manifest: true, files: fullFactsBeforeRestore.uploads_files },
        database_facts_unchanged: true,
      };
    }, '08-backup-corrupt-restore-rejected.png');

    assert.deepEqual(pageErrors, [], `Unexpected browser page errors: ${pageErrors.join(' | ')}`);
    report.final_database = finalDbSnapshot;
    report.passed = true;
    report.completed_at = new Date().toISOString();
    await context.tracing.stop({ path: path.join(evidence, 'trace.zip') });
    await context.close();
    await browser.close();
    browser = null;
    fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
    console.log(JSON.stringify({ passed: true, run_id: runId, scenarios: report.scenarios.length, evidence }));
  } catch (error) {
    report.passed = false;
    report.failed_at = new Date().toISOString();
    report.error = String(error?.stack ?? error);
    if (page) await page.screenshot({ path: path.join(outputDir, 'fatal-failure.png'), fullPage: true }).catch(() => {});
    const tracePath = path.join(evidence, 'trace.zip');
    await page?.context().tracing.stop({ path: tracePath }).catch(() => {});
    if (browser) await browser.close().catch(() => {});
    fs.writeFileSync(path.join(evidence, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
    throw error;
  } finally {
    stopServer();
    if (server.exitCode === null) await once(server, 'exit');
    serverLog.end();
    process.removeListener('SIGINT', stopServer);
    process.removeListener('SIGTERM', stopServer);
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
