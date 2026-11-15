// Continue a live-cloud run from its isolated synthetic extraction database.
// This path deliberately reuses the prior task/table and never uploads or extracts again.
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID, createHash } = require('node:crypto');
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const {
  directoryFingerprint,
  apiBootstrapSource,
  exportWorkbookFacts,
  observeResponse,
  pythonInstrumentation,
  readInstrumentationMarkers,
  readCalls,
  safeError,
  scanArtifactsForSecret,
  sourceFingerprint,
  startLogged,
  stopChild,
  summarizeBudgetLedger,
  waitForInstrumentation,
  waitForReady,
  waitForWorker,
} = require('./verify-v060-live-cloud.cjs');

const project = path.resolve(__dirname, '..');
const python = process.env.ZHIYI_TEST_PYTHON || path.join(project, 'apps/api/.venv/Scripts/python.exe');
const API_SRC = path.join(project, 'apps/api/src');
const evidenceBase = path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-cloud');
const metadataBudget = 200;

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--key-file') args.keyFile = argv[++index];
    else if (argv[index] === '--source-run') args.sourceRun = argv[++index];
    else if (argv[index] === '--call-budget') args.callBudget = Number(argv[++index]);
    else throw new Error('Use --key-file <path> --source-run <run-id> and optionally --call-budget 10..15.');
  }
  if (!args.keyFile || !args.sourceRun) throw new Error('An explicit key file and existing isolated source run ID are required.');
  if (!isRunIdSegment(args.sourceRun)) throw new Error('The selected source run must be one valid run-id path segment.');
  args.callBudget ??= 15;
  if (!Number.isInteger(args.callBudget) || args.callBudget < 10 || args.callBudget > 15) throw new Error('The real-model request budget must be between 10 and 15.');
  args.keyFile = path.resolve(project, args.keyFile);
  return args;
}

function isRunIdSegment(value) {
  return typeof value === 'string' && value !== '.' && value !== '..' && !/[\\/]/.test(value)
    && /^[0-9TZ:.-]+-[a-f0-9]{8}$/.test(value);
}

function reportPathForRun(runId) {
  if (!isRunIdSegment(runId)) throw new Error('A linked source report contains an invalid run-id path segment.');
  const root = fs.realpathSync(evidenceBase);
  const resolved = fs.realpathSync(path.resolve(evidenceBase, runId));
  const relative = path.relative(root, resolved);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative) || path.dirname(resolved) !== root) {
    throw new Error('A linked source report resolves outside the evidence root.');
  }
  const reportName = ['continuation.json', 'report.json'].find((name) => fs.existsSync(path.join(resolved, name)));
  if (!reportName) throw new Error('A linked source run has no continuation or base report.');
  const reportPath = fs.realpathSync(path.join(resolved, reportName));
  if (path.dirname(reportPath) !== resolved) throw new Error('A linked report file resolves outside its run directory.');
  return reportPath;
}

function findBaseReport(sourceReport, sourceRun) {
  let current = sourceReport;
  let currentRun = sourceRun;
  const chain = [sourceRun];
  const seen = new Set(chain);
  while (!current.taskId || !current.createdTemplateId || !current.synthetic?.templateName) {
    const parentRun = current.priorRun;
    if (!isRunIdSegment(parentRun) || seen.has(parentRun)) throw new Error('The selected continuation has no valid base extraction report.');
    current = JSON.parse(fs.readFileSync(reportPathForRun(parentRun), 'utf8'));
    if (current.run !== parentRun) throw new Error('A linked source report does not match its run ID.');
    currentRun = parentRun;
    chain.push(currentRun);
    seen.add(currentRun);
  }
  return { report: current, chain };
}

function exactExportChecks(exportFacts, expected) {
  assert(exportFacts.rows.length >= 2, 'Downloaded workbook has no data row.');
  const headers = exportFacts.rows[0].map((value) => String(value ?? ''));
  const row = exportFacts.rows[1];
  const golden = {
    单据编号: expected.header.doc_id,
    经办人: expected.header.operator,
    物品: expected.items[0].item,
    数量: expected.items[0].qty,
    金额: expected.items[0].amount,
  };
  const checks = {};
  for (const [label, value] of Object.entries(golden)) {
    const index = headers.indexOf(label);
    assert(index >= 0, `Downloaded workbook is missing the ${label} column.`);
    assert.equal(row[index], value, `Downloaded workbook ${label} differs from the exact golden.`);
    checks[label] = row[index];
  }
  return { headers, checks };
}

async function runContinuation() {
  const args = parseArgs(process.argv.slice(2));
  if (!fs.existsSync(args.keyFile)) throw new Error('The selected key file does not exist.');
  if (!fs.existsSync(python)) throw new Error('The API virtual environment is incomplete.');
  const priorRun = args.sourceRun;
  const priorEvidenceRoot = path.join(evidenceBase, priorRun);
  const evidenceBaseReal = fs.realpathSync(evidenceBase);
  const priorEvidenceReal = fs.realpathSync(priorEvidenceRoot);
  const sourceRelativeToEvidence = path.relative(evidenceBaseReal, priorEvidenceReal);
  if (!sourceRelativeToEvidence || sourceRelativeToEvidence.startsWith('..') || path.isAbsolute(sourceRelativeToEvidence)
    || path.dirname(priorEvidenceReal) !== evidenceBaseReal) throw new Error('The selected source run resolves outside the evidence root.');
  const priorTempRoot = path.join(project, '.local/tmp/2026-09-30-deep-cloud', priorRun);
  const priorReportPath = reportPathForRun(priorRun);
  if (!fs.existsSync(priorReportPath)) throw new Error('The selected run has no saved report.');
  const prior = JSON.parse(fs.readFileSync(priorReportPath, 'utf8'));
  if (prior.run !== priorRun || !prior.source?.frozenWebDir) throw new Error('The selected run does not contain reusable frozen-build evidence.');
  if (!prior.runtimeProcesses?.workerExited || !prior.runtimeProcesses?.apiExited) throw new Error('The original isolated processes were not confirmed stopped before snapshot.');
  const baseSelection = findBaseReport(prior, priorRun);
  const baseReport = baseSelection.report;
  const taskId = prior.taskId ?? prior.priorTaskId ?? baseReport.taskId;
  const templateId = prior.createdTemplateId ?? baseReport.createdTemplateId;
  const synthetic = prior.synthetic ?? baseReport.synthetic;
  if (!taskId || !templateId || !synthetic?.templateName) throw new Error('The selected run chain does not resolve to its synthetic extraction.');
  const sourceReviewWasEdited = prior.reviewEdit?.editedReviewResultExact === true || prior.currentReviewExpected?.header?.operator === '合成复核员';
  const sourceProfileId = prior.createdProfileId ?? prior.profileId ?? baseReport.createdProfileId;
  if (!sourceProfileId) throw new Error('The selected run chain has no saved cloud profile identity.');
  const frozenWebDir = path.resolve(project, prior.source.frozenWebDir);
  if (!fs.existsSync(path.join(frozenWebDir, 'index.html'))) throw new Error('The prior frozen web build is missing.');
  const frozenWebDistSha256 = directoryFingerprint(frozenWebDir);
  if (frozenWebDistSha256 !== prior.source.frozenWebDistSha256) throw new Error('The frozen web build no longer matches the prior evidence.');
  const continuationId = randomUUID().slice(0, 8);
  const run = `${new Date().toISOString().replaceAll(':', '-')}-${continuationId}`;
  const evidenceRoot = path.join(evidenceBase, run);
  const tempRoot = path.join(project, '.local/tmp/2026-09-30-deep-cloud', run);
  const continuationDir = evidenceRoot;
  const sourceDataDir = path.join(priorTempRoot, 'data');
  const dataDir = path.join(tempRoot, 'data');
  const instrumentationDir = path.join(tempRoot, 'instrumentation');
  const globalLedgerPath = path.join(evidenceBase, 'cloud-budget-ledger.jsonl');
  const globalHistoryPath = path.join(project, '.local/cloud-budget-history.json');
  const localLedgerPath = path.join(evidenceRoot, 'cloud-calls.jsonl');
  fs.mkdirSync(continuationDir, { recursive: true });
  fs.mkdirSync(tempRoot, { recursive: true });
  fs.mkdirSync(instrumentationDir, { recursive: true });
  const priorReservations = prior.globalBudget?.conservativeReservations ?? prior.globalBudget?.conservativeReservationsForUntrackedRuns ?? { models: 0, completions: 0 };
  const priorHasBudgetReservation = Number(priorReservations.models ?? 0) > 0 || Number(priorReservations.completions ?? 0) > 0;
  if (!fs.existsSync(sourceDataDir) || !fs.existsSync(globalLedgerPath) || (priorHasBudgetReservation && !fs.existsSync(globalHistoryPath))) {
    throw new Error('The prior isolated database or required cloud budget evidence is missing.');
  }
  const sourceDatabasePath = path.join(sourceDataDir, 'document-pipeline.db');
  const snapshotDatabasePath = path.join(dataDir, 'document-pipeline.db');
  if (!fs.existsSync(sourceDatabasePath)) throw new Error('The prior isolated extraction database is missing.');
  const sourceDatabaseSha256 = createHash('sha256').update(fs.readFileSync(sourceDatabasePath)).digest('hex');
  fs.cpSync(sourceDataDir, dataDir, { recursive: true, errorOnExist: true });
  const snapshotDatabaseSha256 = createHash('sha256').update(fs.readFileSync(snapshotDatabasePath)).digest('hex');
  if (sourceDatabaseSha256 !== snapshotDatabaseSha256) throw new Error('SQLite database snapshot hash does not match its stopped source.');
  const earlierCalls = readCalls(localLedgerPath);
  if (earlierCalls.error) throw new Error('New continuation call ledger is unreadable.');
  const earlierDispatchCount = earlierCalls.calls.length;
  const globalBefore = summarizeBudgetLedger(globalLedgerPath, globalHistoryPath);
  if (globalBefore.error) throw new Error('The cumulative cloud budget ledger is unreadable.');
  if (globalBefore.actual.completions + globalBefore.reserved.completions >= 15
    || globalBefore.actual.models + globalBefore.reserved.models >= metadataBudget) throw new Error('Cumulative cloud request budget is exhausted.');
  const profileName = `云端助手续测-${run.slice(-8)}-${continuationId}`;
  const originalWebDistMtime = fs.statSync(path.join(frozenWebDir, 'index.html')).mtime.toISOString();
  const source = {
    gitHead: spawnSync('git', ['rev-parse', 'HEAD'], { cwd: project, encoding: 'utf8', windowsHide: true }).stdout.trim(),
    runtimeSourceSha256: createHash('sha256').update(sourceFingerprint()).update(fs.readFileSync(__filename)).digest('hex'),
    frozenWebDistSha256,
    frozenWebDir: path.relative(project, frozenWebDir).replaceAll('\\', '/'),
    frozenWebIndexMtime: originalWebDistMtime,
    provenance: 'Current source and reused frozen web-dist hashes are independent observations; no build provenance is inferred.',
  };
  fs.writeFileSync(path.join(instrumentationDir, 'sitecustomize.py'), pythonInstrumentation(args.callBudget, metadataBudget), { encoding: 'utf8', flag: 'wx' });
  const apiBootstrapPath = path.join(instrumentationDir, 'api-bootstrap.py');
  fs.writeFileSync(apiBootstrapPath, apiBootstrapSource(), { encoding: 'utf8', flag: 'wx' });
  const runLedgerLock = `${localLedgerPath}.lock`;
  if (!fs.existsSync(runLedgerLock)) fs.writeFileSync(runLedgerLock, '0', { encoding: 'ascii', flag: 'wx' });
  const env = { ...process.env, PYTHONUTF8: '1' };
  for (const name of Object.keys(env)) if (name.startsWith('DOCUMENT_PIPELINE_') || name.startsWith('ZHIYI_CLOUD_AUDIT_')) delete env[name];
  env.DOCUMENT_PIPELINE_DATA_DIR = dataDir;
  env.DOCUMENT_PIPELINE_QUEUE_DB = path.join(dataDir, 'queue.db');
  env.DOCUMENT_PIPELINE_WORKER_HEARTBEAT = path.join(dataDir, 'worker-heartbeat.json');
  env.DOCUMENT_PIPELINE_WEB_DIR = frozenWebDir;
  env.DOCUMENT_PIPELINE_MODEL_PROVIDER = 'openai_compatible';
  env.DOCUMENT_PIPELINE_MODEL_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
  env.DOCUMENT_PIPELINE_MODEL_NAME = 'qwen3.6-flash';
  env.ZHIYI_CLOUD_AUDIT_LEDGER = localLedgerPath;
  env.ZHIYI_CLOUD_AUDIT_GLOBAL_LEDGER = globalLedgerPath;
  env.ZHIYI_CLOUD_AUDIT_GLOBAL_HISTORY = globalHistoryPath;
  env.ZHIYI_CLOUD_AUDIT_MARKER = path.join(instrumentationDir, 'loaded.jsonl');
  env.ZHIYI_CLOUD_AUDIT_RUN_ID = run;
  env.ZHIYI_CLOUD_AUDIT_COMPLETION_BUDGET = String(args.callBudget);
  env.ZHIYI_CLOUD_AUDIT_METADATA_BUDGET = String(metadataBudget);
  env.PYTHONPATH = [instrumentationDir, API_SRC, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
  delete env.DOCUMENT_PIPELINE_MODEL_API_KEY;
  delete env.ZHIYI_OFFLINE;
  delete env.ZHIYI_FAST;

  const portServer = require('node:net').createServer();
  portServer.listen(0, '127.0.0.1');
  await new Promise((resolve, reject) => { portServer.once('listening', resolve); portServer.once('error', reject); });
  const port = portServer.address().port;
  await new Promise((resolve) => portServer.close(resolve));
  const baseUrl = `http://127.0.0.1:${port}`;
  const apiEnv = { ...env, ZHIYI_CLOUD_AUDIT_PROCESS_ROLE: 'api' };
  const workerEnv = { ...env, ZHIYI_CLOUD_AUDIT_PROCESS_ROLE: 'worker' };
  const api = startLogged(python, [apiBootstrapPath, String(port), API_SRC], { cwd: project, env: apiEnv, stdio: ['ignore', 'pipe', 'pipe'] }, path.join(continuationDir, 'api.log'));
  const worker = startLogged(python, ['-m', 'huey.bin.huey_consumer', 'document_pipeline_api.worker.huey', '-w', '1', '-k', 'thread', '-d', '0.01', '-m', '0.01', '-b', '1'], { cwd: project, env: workerEnv, stdio: ['ignore', 'pipe', 'pipe'] }, path.join(continuationDir, 'worker.log'));
  let browser;
  let page;
  let keyBuffer;
  let keyText = '';
  let redactionSecret = '';
  let keyEntered = false;
  let createdProfileId = null;
  let profileHttpEvents = [];
  let assistantThreadId = null;
  let assistantRunId = null;
  const sseAttempts = [];
  const browserThreadRefreshes = [];
  let firstSseReset = false;
  const report = {
    schema: 'zhiyi.v060.live-cloud-continuation.v1',
    run,
    priorRun,
    continuationId,
    passed: false,
    evidenceRoot: path.relative(project, continuationDir).replaceAll('\\', '/'),
    sqliteSnapshot: {
      source: path.relative(project, sourceDatabasePath).replaceAll('\\', '/'),
      copiedTo: path.relative(project, snapshotDatabasePath).replaceAll('\\', '/'),
      sourceSha256: sourceDatabaseSha256,
      snapshotSha256: snapshotDatabaseSha256,
      stoppedSourceCopiedBeforeRuntimeStart: true,
    },
    runtimeRoot: path.relative(project, tempRoot).replaceAll('\\', '/'),
    priorTaskId: taskId,
    taskId,
    createdTemplateId: templateId,
    baseRun: baseReport.run,
    sourceRunChain: baseSelection.chain,
    synthetic,
    sourceReviewWasEdited,
    priorCompletionReused: true,
    extractionNotRepeated: true,
    source,
    browser: { engine: 'Playwright Chromium', headless: true, trace: false, har: false, videos: false },
    budgetBeforeContinuation: globalBefore,
    model: { provider: '阿里云百炼 OpenAI 兼容接口', model: 'qwen3.6-flash', syntheticOnly: true, requestBudgets: { chatCompletions: args.callBudget, modelMetadata: metadataBudget } },
    credential: { screenshotsWhileKeyPresent: false, cleanup: 'pending' },
  };
  let failure = null;
  let cleanupRefs = -1;
  try {
    await waitForReady(baseUrl, api, 'Continuation API');
    await waitForWorker(baseUrl, worker, env.DOCUMENT_PIPELINE_WORKER_HEARTBEAT);
    report.instrumentationReadyProcesses = await waitForInstrumentation(env.ZHIYI_CLOUD_AUDIT_MARKER, [api, worker]);
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, acceptDownloads: true });
    page = await context.newPage();
    page.on('request', (request) => {
      if (request.url().includes('/api/v1/models/profiles') && ['POST', 'PUT'].includes(request.method())) {
        profileHttpEvents.push({ event: 'request', method: request.method(), route: new URL(request.url()).pathname });
      }
    });
    page.on('response', (response) => {
      if (response.url().includes('/api/v1/models/profiles') && ['POST', 'PUT'].includes(response.request().method())) {
        profileHttpEvents.push({ event: 'response', method: response.request().method(), route: new URL(response.url()).pathname, status: response.status() });
      }
      if (response.url().includes('/assistant/runs/') && response.url().includes('/events')) {
        sseAttempts.push({ status: response.status(), contentType: response.headers()['content-type'] ?? '' });
      }
      if (assistantThreadId && assistantRunId) {
        const url = new URL(response.url());
        if (url.pathname === `/api/v1/assistant/threads/${assistantThreadId}` && url.searchParams.get('run_id') === assistantRunId) {
          browserThreadRefreshes.push(response.json().then((body) => ({ status: response.status(), runStatus: body.runs?.find((item) => item.id === assistantRunId)?.status ?? null })).catch(() => ({ status: response.status(), runStatus: null })));
        }
      }
    });
    async function apiRequest(method, route, data) {
      const response = await page.request.fetch(`${baseUrl}/api/v1${route}`, { method, data });
      if (!response.ok()) throw new Error(`${method} ${route} returned HTTP ${response.status()}.`);
      return response.status() === 204 ? null : response.json();
    }

    const originalExpected = { header: { doc_id: 'SYN-2026-001', operator: '合成演示员' }, items: [{ item: '蓝色标签', qty: 2, amount: 6.5 }] };
    const revisedExpected = { header: { doc_id: 'SYN-2026-001', operator: '合成复核员' }, items: [{ item: '蓝色标签', qty: 2, amount: 6.5 }] };
    let expected = sourceReviewWasEdited ? revisedExpected : originalExpected;
    const task = (await apiRequest('GET', '/tasks')).find((item) => item.id === taskId);
    assert.equal(task?.status, 'completed', 'Reused synthetic task is not completed.');
    const reviewed = await apiRequest('GET', `/tasks/${taskId}/result`);
    assert.deepEqual(reviewed.original_result, originalExpected, 'Immutable model extraction differs from synthetic golden.');
    assert.deepEqual(reviewed.result, expected, 'Persisted current review differs from the source run golden.');
    const confirmation = await apiRequest('GET', `/tasks/${taskId}/confirmation`);
    assert.equal(confirmation.task_id, taskId);
    assert.equal(confirmation.row_count, 1);
    assert.equal(typeof confirmation.confirmed_at, 'string');
    const table = await apiRequest('GET', `/tables/${confirmation.table_id}`);
    assert.equal(table.rows.length, 1);
    const projectionGolden = { ...expected.header, ...expected.items[0] };
    const projectedRow = table.rows[0].values;
    for (const [key, value] of Object.entries(projectionGolden)) assert.equal(projectedRow[key], value, `Current table projection differs from golden field ${key}.`);
    report.reusedExtraction = {
      originalCompletion: prior.extraction?.terminalStatus,
      modelExactBeforeReview: prior.extraction?.modelExactBeforeReview,
      originalModelResultExact: true,
      sourceCurrentResultExact: true,
      sourceReviewWasEdited,
      confirmation: { taskId: confirmation.task_id, tableId: confirmation.table_id, rowCount: confirmation.row_count, confirmedAtPresent: Boolean(confirmation.confirmed_at) },
      tableProjectionFieldsExact: Object.keys(projectionGolden),
      extraProjectionFields: Object.keys(projectedRow).filter((key) => !(key in projectionGolden)),
      rowStorageShape: 'table row values project shared header + detail values + source_filename/item_index; comparison asserted the five business fields by schema contract',
    };

    const templateName = synthetic.templateName;
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    if (sourceReviewWasEdited) {
      expected = revisedExpected;
      report.reviewEdit = {
        ...prior.reviewEdit,
        performedThroughBrowserHistoryDetail: true,
        reusedFromSourceRun: priorRun,
        noSecondEdit: true,
        originalModelResultUnchanged: true,
        editedReviewResultExact: true,
        currentTableProjectionExact: true,
      };
    } else {
      await page.locator('.app-sidebar').getByRole('button', { name: '文件历史', exact: true }).click();
      await page.getByLabel('按文件名搜索').fill('synthetic-materials-v060.txt');
      const historyFile = page.getByRole('button', { name: 'synthetic-materials-v060.txt', exact: true });
      await historyFile.waitFor({ state: 'visible' });
      await historyFile.click();
      const originalCard = page.locator('.history-original-card:not(.hidden)');
      const headerRows = originalCard.locator('.extract-header-row');
      await headerRows.first().waitFor({ state: 'visible' });
      let operatorRowIndex = -1;
      for (let index = 0; index < await headerRows.count(); index += 1) {
        if ((await headerRows.nth(index).locator('.extract-header-label').textContent())?.trim() === '经办人') {
          operatorRowIndex = index;
          break;
        }
      }
      assert(operatorRowIndex >= 0, 'The history detail has no stable 经办人 field label.');
      const operatorRow = headerRows.nth(operatorRowIndex);
      const operatorValue = operatorRow.locator('.extract-header-value');
      assert.equal((await operatorValue.textContent())?.trim(), originalExpected.header.operator);
      await page.screenshot({ path: path.join(continuationDir, 'history-detail-before-edit.png') });
      await operatorValue.waitFor({ state: 'visible' });
      await operatorValue.click();
      const operatorHandle = await operatorValue.elementHandle();
      await page.waitForFunction((element) => element?.isContentEditable === true, operatorHandle, { timeout: 5000 });
      const editorState = await operatorValue.evaluate((element) => ({
        contentEditableAttribute: element.getAttribute('contenteditable'),
        isContentEditable: element.isContentEditable,
        text: element.textContent,
      }));
      report.reviewEditUiState = { rowVisible: true, valueVisible: await operatorValue.isVisible(), ...editorState };
      await page.screenshot({ path: path.join(continuationDir, 'history-detail-after-edit-click.png') });
      assert.equal(editorState.isContentEditable, true, 'The history operator field did not enter its contenteditable state.');
      const operatorEditor = operatorValue;
      await operatorEditor.fill('合成复核员');
      await operatorEditor.press('Enter');
      await page.getByText('合成复核员', { exact: true }).waitFor();
      const revisionSave = observeResponse(page, (response) => response.url().endsWith(`/api/v1/tasks/${taskId}/review`) && response.request().method() === 'PUT');
      await page.getByRole('button', { name: '修订到表', exact: true }).click();
      const revisionResponse = await revisionSave;
      if (!revisionResponse.response?.ok()) throw new Error(`Browser review edit did not save successfully${revisionResponse.response ? ` (HTTP ${revisionResponse.response.status()})` : ''}.`);
      expected = revisedExpected;
      const afterEdit = await apiRequest('GET', `/tasks/${taskId}/result`);
      assert.deepEqual(afterEdit.original_result, originalExpected, 'Review edit changed the immutable original model result.');
      assert.deepEqual(afterEdit.result, expected, 'Review edit did not persist the exact revised golden.');
      const afterEditConfirmation = await apiRequest('GET', `/tasks/${taskId}/confirmation`);
      assert.equal(afterEditConfirmation.row_count, 1);
      const afterEditTable = await apiRequest('GET', `/tables/${afterEditConfirmation.table_id}`);
      assert.equal(afterEditTable.rows.length, 1);
      const afterEditProjection = afterEditTable.rows[0].values;
      for (const [key, value] of Object.entries({ ...expected.header, ...expected.items[0] })) assert.equal(afterEditProjection[key], value, `Saved current table differs from the edited golden field ${key}.`);
      assert.notEqual(afterEditProjection.operator, originalExpected.header.operator, 'Current table retained the original operator after the browser edit.');
      report.reviewEdit = {
        performedThroughBrowserHistoryDetail: true,
        field: 'operator',
        before: originalExpected.header.operator,
        after: expected.header.operator,
        saveHttpStatus: revisionResponse.response.status(),
        originalModelResultUnchanged: true,
        editedReviewResultExact: true,
        currentTableProjectionExact: true,
        currentTableFieldCompared: Object.keys({ ...expected.header, ...expected.items[0] }),
      };
    }
    const profilesLoaded = observeResponse(page, (response) => response.url().endsWith('/api/v1/models/profiles') && response.request().method() === 'GET');
    await page.locator('.app-sidebar').getByRole('button', { name: '设置', exact: true }).click({ timeout: 10000 });
    const profilesResult = await profilesLoaded;
    if (!profilesResult.response?.ok()) throw new Error('Model profiles did not load in continuation UI.');
    const existingProfiles = await profilesResult.response.json();
    const sourceProfile = existingProfiles.find((item) => item.id === sourceProfileId && item.is_remote);
    assert(sourceProfile, 'Source run cloud profile missing from reused DB.');
    const priorProfile = page.locator('.profile-list .profile-item').filter({ hasText: sourceProfile.name });
    await priorProfile.waitFor({ state: 'visible' });
    await page.locator('#settings-ai').scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: '新建空白方案', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#model-profile-name')?.value === '' && document.querySelector('#model-profile-url')?.value === '' && document.querySelector('#model-profile-model')?.value === '');
    await page.locator('#model-profile-name').fill(profileName);
    await page.locator('#model-profile-provider').selectOption('aliyun');
    await page.locator('#model-profile-url').fill('https://dashscope.aliyuncs.com/compatible-mode/v1');
    await page.locator('#model-profile-model').fill('qwen3.6-flash');
    await page.locator('#model-profile-reasoning').selectOption('none');
    const keyInput = page.locator('#model-profile-key');
    assert.equal(await keyInput.getAttribute('type'), 'password');
    keyBuffer = fs.readFileSync(args.keyFile);
    keyText = keyBuffer.toString('utf8').replace(/^\uFEFF/, '').trim();
    if (!keyText) throw new Error('The selected key file is empty.');
    redactionSecret = keyText;
    keyEntered = true;
    await keyInput.fill(keyText);
    const createResponse = observeResponse(page, (response) => response.url().endsWith('/api/v1/models/profiles') && response.request().method() === 'POST');
    await page.getByRole('button', { name: '创建方案', exact: true }).click();
    const created = await createResponse;
    if (!created.response) throw new Error(`Continuation cloud profile create produced no response (${created.errorType ?? 'unknown'}).`);
    if (!created.response.ok()) throw new Error(`Continuation cloud profile create returned HTTP ${created.response.status()}.`);
    createdProfileId = (await created.response.json()).id;
    await page.getByText('方案已创建，请确认后激活。', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('#model-profile-key')?.value === '', null, { timeout: 10000 });
    keyBuffer.fill(0);
    keyBuffer = null;
    keyText = '';
    assert.equal(await keyInput.inputValue(), '');
    const saved = (await apiRequest('GET', '/models/profiles')).find((item) => item.id === createdProfileId);
    assert(saved?.has_api_key && saved.is_remote);
    await page.getByRole('button', { name: '激活方案', exact: true }).click();
    const activateDialog = page.getByRole('dialog', { name: '确认使用云端模型？' });
    await activateDialog.waitFor();
    await activateDialog.getByRole('button', { name: '我确认，使用云端服务' }).click();
    await page.getByText('已激活；之后新上传的文件会使用这套方案。', { exact: true }).waitFor();
    const active = (await apiRequest('GET', '/models/profiles')).find((item) => item.id === createdProfileId);
    assert(active?.is_active && active.is_remote);
    profileHttpEvents = profileHttpEvents.filter((item) => item.route.endsWith('/api/v1/models/profiles') || item.route.endsWith('/activate'));
    report.profileHttpEvents = profileHttpEvents;
    report.chatProfile = { createdThroughBrowser: true, activationConfirmationAcceptedInUI: true, active: active.is_active, remote: active.is_remote, passwordFieldCleared: true, hasApiKey: saved.has_api_key };
    await page.screenshot({ path: path.join(continuationDir, 'cloud-profile-active.png') });

    const exportPath = path.join(continuationDir, 'synthetic-reviewed-golden.xlsx');
    const reuseExport = sourceReviewWasEdited && prior.export?.exportedExact === true;
    if (reuseExport) {
      const sourceExportPath = fs.realpathSync(path.join(priorEvidenceReal, 'synthetic-reviewed-golden.xlsx'));
      assert.equal(path.dirname(sourceExportPath), priorEvidenceReal, 'Source export resolves outside its evidence run.');
      const sourceExportSha256 = createHash('sha256').update(fs.readFileSync(sourceExportPath)).digest('hex');
      assert.equal(sourceExportSha256, prior.export.sha256, 'Previously verified XLSX no longer matches its evidence hash.');
      fs.copyFileSync(sourceExportPath, exportPath, fs.constants.COPYFILE_EXCL);
    } else {
      await page.locator('.app-sidebar').getByRole('button', { name: '数据仓库', exact: true }).click();
      await page.locator('.sheet-tree-item').filter({ hasText: templateName }).click();
      await page.getByRole('button', { name: '导出', exact: true }).click();
      const downloadPromise = page.waitForEvent('download').then((download) => ({ download }), (error) => ({ errorType: error?.name ?? 'download_failed' }));
      await page.getByRole('button', { name: /导出 Excel/ }).click();
      const downloadCapture = await downloadPromise;
      if (!downloadCapture.download) throw new Error(`Continuation XLSX download was not observed (${downloadCapture.errorType ?? 'unknown'}).`);
      await downloadCapture.download.saveAs(exportPath);
      await page.screenshot({ path: path.join(continuationDir, 'cloud-export-golden.png') });
    }
    const exportFacts = exportWorkbookFacts(exportPath, env);
    const exportCheck = exactExportChecks(exportFacts, expected);
    report.export = { exportedExact: true, sha256: createHash('sha256').update(fs.readFileSync(exportPath)).digest('hex'), bytes: fs.statSync(exportPath).size, sheet: exportFacts.sheet, columns: exportCheck.headers, exactFacts: exportCheck.checks,
      ...(reuseExport ? { reusedFromSourceRun: priorRun, noSecondDownload: true } : {}) };

    await page.locator('.app-sidebar').getByRole('button', { name: '问知意', exact: true }).click();
    await page.locator('.ask-settings-trigger').click();
    const modelSelect = page.getByLabel('问知意模型方案');
    const refreshedProfiles = observeResponse(page, (response) => response.url().endsWith('/api/v1/models/profiles') && response.request().method() === 'GET');
    await modelSelect.focus();
    const refreshedProfilesResult = await refreshedProfiles;
    if (!refreshedProfilesResult.response?.ok()) throw new Error('Assistant model choices did not refresh after focusing the selector.');
    const refreshedProfilesBody = await refreshedProfilesResult.response.json();
    assert(refreshedProfilesBody.some((item) => item.id === createdProfileId), 'Assistant model selector refresh omitted the newly created profile.');
    const createdOption = modelSelect.locator(`option[value="${createdProfileId}"]`);
    await createdOption.waitFor({ state: 'attached' });
    await modelSelect.selectOption(createdProfileId);
    assert.equal(await modelSelect.inputValue(), createdProfileId, 'Assistant model selector did not retain the selected cloud profile.');
    await page.getByRole('tab', { name: '数据表' }).click();
    await page.getByLabel('搜索可选资料').fill(templateName);
    const resource = page.locator('.ask-resource-list label').filter({ hasText: templateName });
    await resource.waitFor();
    await resource.locator('input[type="checkbox"]').check();
    await page.getByRole('button', { name: '只读分析', exact: true }).click();
    await page.getByRole('button', { name: '完成', exact: true }).click();
    const remoteConsent = page.locator('.ask-remote-consent input[type="checkbox"]');
    await remoteConsent.click();
    await remoteConsent.waitFor({ state: 'detached' });
    report.remoteConsentGrantedThroughUi = { checkboxClicked: true, componentDetachedAfterGrant: true };
    await page.route('**/api/v1/assistant/runs/*/events*', async (route) => {
      if (!firstSseReset) {
        firstSseReset = true;
        sseAttempts.push({ injected: true, status: 'connection_reset' });
        await route.abort('connectionreset');
      } else await route.continue();
    });
    const question = '请调用只读工具 read_data_rows，读取当前授权表的第一条记录和业务字段。根据工具结果简洁报告该记录的单据编号、经办人、物品、数量和金额，并说明来源表名；不要修改任何内容，也不要猜测。';
    await page.getByLabel('向问知意提问').fill(question);
    const sendButton = page.getByRole('button', { name: '发送问题', exact: true });
    assert(await sendButton.isEnabled(), 'The assistant send button is disabled after model, read-only scope, and remote-consent selection.');
    const startResponsePromise = observeResponse(page, (response) => response.url().endsWith('/api/v1/assistant/runs') && response.request().method() === 'POST');
    await sendButton.click();
    const startResponse = await startResponsePromise;
    if (!startResponse.response) throw new Error(`Assistant run did not start (${startResponse.errorType ?? 'unknown'}).`);
    if (!startResponse.response.ok()) throw new Error(`Assistant start returned HTTP ${startResponse.response.status()}.`);
    const started = await startResponse.response.json();
    assistantThreadId = started.thread_id;
    assistantRunId = started.run_id;
    let thread = null;
    const startedAt = Date.now();
    while (Date.now() - startedAt < 180000) {
      thread = await apiRequest('GET', `/assistant/threads/${assistantThreadId}?run_id=${assistantRunId}`);
      const runDetail = thread.runs?.find((item) => item.id === assistantRunId);
      if (runDetail && !['running', 'waiting', 'cancelling'].includes(runDetail.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    const completed = thread?.runs?.find((item) => item.id === assistantRunId);
    assert.equal(completed?.status, 'completed', 'Live assistant read-only tool run did not complete.');
    const toolParts = (thread.messages ?? []).flatMap((message) => message.parts ?? []).filter((part) => part.type === 'tool');
    const tool = toolParts.find((part) => part.name === 'read_data_rows' && part.status === 'completed');
    assert(tool, 'Live assistant did not complete read_data_rows.');
    const toolValues = tool.result?.rows?.[0]?.values;
    assert(toolValues && typeof toolValues === 'object', 'read_data_rows did not return row values.');
    const toolGolden = { ...expected.header, ...expected.items[0] };
    for (const [key, value] of Object.entries(toolGolden)) assert.equal(toolValues[key], value, `read_data_rows result differs from synthetic golden field ${key}.`);
    const usage = completed.usage ?? {};
    assert(Array.isArray(usage.calls) && usage.calls.length >= 1, 'Provider call ledger is missing from the assistant run.');
    assert(usage.calls.every((call) => typeof call.status === 'string'));
    const tokenKeys = ['prompt_tokens', 'completion_tokens', 'total_tokens'];
    for (const call of usage.calls) {
      for (const key of tokenKeys) {
        if (key in call) assert(Number.isSafeInteger(call[key]) && call[key] >= 0, `Provider ${key} for call ${call.index} is invalid.`);
      }
    }
    const callsWithUsage = usage.calls.filter((call) => Number.isSafeInteger(call.prompt_tokens) && call.prompt_tokens >= 0).length;
    assert.equal(usage.model_calls, usage.calls.length, 'Persisted provider model_calls does not match its call ledger.');
    assert.equal(usage.calls_with_usage, callsWithUsage, 'Persisted calls_with_usage does not match per-call token facts.');
    assert.equal(usage.usage_complete, callsWithUsage === usage.calls.length, 'Persisted usage_complete does not match per-call usage availability.');
    const tokenTotals = {};
    for (const key of tokenKeys) {
      const supplied = usage.calls.filter((call) => Number.isSafeInteger(call[key]) && call[key] >= 0);
      if (supplied.length) {
        tokenTotals[key] = supplied.reduce((sum, call) => sum + call[key], 0);
        assert.equal(usage[key], tokenTotals[key], `Persisted ${key} total differs from per-call values.`);
      } else {
        assert(usage[key] == null, `Persisted ${key} total has no per-call source.`);
        tokenTotals[key] = null;
      }
    }
    const liveCallLedger = readCalls(localLedgerPath);
    assert.equal(liveCallLedger.error, null, 'Per-run external call ledger is unreadable.');
    const successfulCloudCompletions = liveCallLedger.calls.filter((item) => item.endpoint === 'chat/completions' && item.result?.status === 'http_response' && item.result?.http_status === 200);
    assert(successfulCloudCompletions.length >= 1, 'No successful real cloud chat-completion dispatch was recorded for the assistant scenario.');
    const assistantText = (thread.messages ?? []).filter((message) => message.role === 'assistant')
      .flatMap((message) => message.parts ?? [])
      .filter((part) => part.type === 'text' && typeof part.text === 'string')
      .map((part) => part.text).join('\n');
    const numberNearLabel = (label) => {
      const match = new RegExp(`${label}[^\\d-]{0,8}(-?\\d+(?:\\.\\d+)?)`).exec(assistantText);
      return match ? Number(match[1]) : null;
    };
    const assistantTextChecks = {
      documentId: assistantText.includes(expected.header.doc_id),
      operator: assistantText.includes(expected.header.operator),
      item: assistantText.includes(expected.items[0].item),
      quantityNearLabel: numberNearLabel('数量') === Number(expected.items[0].qty),
      amountNearLabel: numberNearLabel('金额') === Number(expected.items[0].amount),
    };
    assert(assistantTextChecks.documentId && assistantTextChecks.operator && assistantTextChecks.item, 'Assistant answer omitted a textual row fact.');
    assert(assistantTextChecks.quantityNearLabel, 'Assistant answer did not report the quantity adjacent to its label.');
    assert(assistantTextChecks.amountNearLabel, 'Assistant answer did not report the amount adjacent to its label.');
    const uiAnswer = page.locator('.ask-message-assistant .ask-message-body').last();
    const browserExpected = { docId: expected.header.doc_id, operator: expected.header.operator, item: expected.items[0].item, quantity: Number(expected.items[0].qty), amount: Number(expected.items[0].amount) };
    await page.waitForFunction((facts) => {
      const text = Array.from(document.querySelectorAll('.ask-message-assistant .ask-message-body')).map((element) => element.innerText).join('\n');
      const near = (label) => { const match = new RegExp(`${label}[^\\d-]{0,8}(-?\\d+(?:\\.\\d+)?)`).exec(text); return match ? Number(match[1]) : null; };
      return text.includes(facts.docId) && text.includes(facts.operator) && text.includes(facts.item)
        && near('数量') === facts.quantity && near('金额') === facts.amount;
    }, browserExpected, { timeout: 30000 });
    const browserAssistantText = await uiAnswer.innerText();
    const browserNumberNearLabel = (label) => {
      const match = new RegExp(`${label}[^\\d-]{0,8}(-?\\d+(?:\\.\\d+)?)`).exec(browserAssistantText);
      return match ? Number(match[1]) : null;
    };
    const browserTextChecks = {
      documentId: browserAssistantText.includes(expected.header.doc_id),
      operator: browserAssistantText.includes(expected.header.operator),
      item: browserAssistantText.includes(expected.items[0].item),
      quantityNearLabel: browserNumberNearLabel('数量') === Number(expected.items[0].qty),
      amountNearLabel: browserNumberNearLabel('金额') === Number(expected.items[0].amount),
    };
    const composer = page.getByLabel('向问知意提问');
    await composer.fill('仅检查可发送状态，不发送。');
    const sendEnabledAfterAnswer = await sendButton.isEnabled();
    await composer.fill('');
    const browserRefreshResults = await Promise.all(browserThreadRefreshes);
    const onErrorCompletedRefresh = browserRefreshResults.some((item) => item.status === 200 && item.runStatus === 'completed');
    const eventStream200Observed = sseAttempts.some((item) => item.contentType?.includes('text/event-stream') && item.status === 200);
    const recoveryMode = eventStream200Observed
      ? 'event-stream-http-200-observed'
      : onErrorCompletedRefresh && Object.values(browserTextChecks).every(Boolean) && sendEnabledAfterAnswer
        ? 'eventsource-onerror-http-completed-refresh-and-browser-ready'
        : 'unverified';
    assert(firstSseReset && recoveryMode !== 'unverified', 'Controlled SSE reset did not lead to an observed stream or HTTP-refresh/browser recovery.');
    assert(Object.values(browserTextChecks).every(Boolean), 'Browser-rendered assistant answer omitted a requested row fact.');
    assert(sendEnabledAfterAnswer, 'Assistant send button did not become available after the answer completed.');
    const threadTitle = thread.title;
    report.assistant = {
      threadId: assistantThreadId,
      runId: assistantRunId,
      status: completed.status,
      profileId: completed.profile_id,
      toolName: tool.name,
      toolStatus: tool.status,
      toolResultFields: toolGolden,
      toolResultExact: true,
      usage: {
        providerCalls: usage.calls.length,
        callsWithUsage,
        usageComplete: usage.usage_complete,
        promptTokens: tokenTotals.prompt_tokens,
        completionTokens: tokenTotals.completion_tokens,
        totalTokens: tokenTotals.total_tokens,
        source: callsWithUsage > 0 ? 'provider streaming usage events persisted by tracked_stream' : 'unknown; provider did not report token usage',
        successfulRealCloudCompletionDispatches: successfulCloudCompletions.length,
      },
      sse: { injectedConnectionReset: firstSseReset, attempts: sseAttempts, observedEventStreamHttp200: eventStream200Observed, browserThreadRefreshes: browserRefreshResults, onErrorCompletedRefresh, browserTextChecks, sendEnabledAfterAnswer, sendEnableCheckDraft: 'synthetic readiness probe; cleared without sending', recoveryMode },
      assistantTextChecks,
    };
    assert.equal(completed.profile_id, createdProfileId);
    await page.unroute('**/api/v1/assistant/runs/*/events*');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.app-sidebar').getByRole('button', { name: '问知意', exact: true }).click();
    const historyThread = page.locator('.ask-thread-title').filter({ hasText: threadTitle });
    await historyThread.waitFor();
    await historyThread.click();
    await page.getByText(question, { exact: true }).waitFor();
    const reloadedThread = await apiRequest('GET', `/assistant/threads/${assistantThreadId}`);
    const reloadedRun = reloadedThread.runs.find((item) => item.id === assistantRunId);
    assert.equal(reloadedRun.status, 'completed');
    assert.deepEqual(reloadedRun.usage, completed.usage, 'Assistant provider usage did not persist exactly after history reload.');
    const persistedTools = reloadedThread.tools.filter((item) => item.name === 'read_data_rows' && item.status === 'completed');
    assert.equal(persistedTools.length, 1);
    for (const [key, value] of Object.entries(toolGolden)) assert.equal(persistedTools[0].result?.rows?.[0]?.values?.[key], value);
    report.historyReload = { threadReopenedInUI: true, toolRecordPersisted: true, toolResultExactPersisted: true, usagePersisted: true, usageDeepEqualAfterReload: true };
    await page.screenshot({ path: path.join(continuationDir, 'cloud-assistant-history.png') });

    report.passed = true;
  } catch (error) {
    failure = safeError(error, redactionSecret || keyText);
    report.error = failure;
  } finally {
    if (keyBuffer) keyBuffer.fill(0);
    keyBuffer = null;
    keyText = '';
    redactionSecret = '';
    if (browser) await browser.close().catch(() => {});
    const workerExited = await stopChild(worker);
    const apiExited = await stopChild(api);
    report.runtimeProcesses = { workerExited, apiExited };
    const cleanupCode = String.raw`import json, sys
from pathlib import Path
from sqlalchemy import create_engine, text
from document_pipeline_api.model_secrets import create_model_secret_store
db = Path(sys.argv[1]).resolve()
if not db.exists():
    print(json.dumps({"deleted": 0}))
    raise SystemExit(0)
profile_id, profile_name = sys.argv[2], sys.argv[3]
engine = create_engine("sqlite:///" + db.as_posix())
try:
    with engine.connect() as connection:
        if profile_id:
            query = text("SELECT DISTINCT secret_ref FROM model_profile_versions WHERE profile_id = :id AND secret_ref IS NOT NULL")
            params = {"id": profile_id}
        else:
            query = text("SELECT DISTINCT secret_ref FROM model_profile_versions WHERE name = :name AND provider = 'openai_compatible' AND model_name = 'qwen3.6-flash' AND secret_ref IS NOT NULL")
            params = {"name": profile_name}
        refs = list(connection.execute(query, params).scalars())
    store = create_model_secret_store()
    for ref in refs:
        store.delete(ref)
    print(json.dumps({"deleted": len(refs)}))
finally:
    engine.dispose()`;
    if (!workerExited || !apiExited) {
      cleanupRefs = -1;
      report.credential.cleanupFailure = 'isolated API/worker remained alive';
    } else {
      const cleaned = spawnSync(python, ['-c', cleanupCode, path.join(dataDir, 'document-pipeline.db'), createdProfileId ?? '', profileName], { cwd: project, env, encoding: 'utf8', windowsHide: true });
      if (cleaned.status === 0 && !cleaned.error) {
        try { cleanupRefs = JSON.parse(cleaned.stdout.trim()).deleted; } catch { cleanupRefs = -1; }
      }
      if (cleanupRefs < 0) report.credential.cleanupFailure = 'run-scoped credential cleanup could not be verified';
    }
    report.credential.cleanup = cleanupRefs >= 0 ? `deleted ${cleanupRefs} credential reference(s) created by this continuation` : 'failed';
    report.credential.cleanupScope = 'only the unique profile created by this continuation; prior profile references were not queried or touched';
    report.credential.newProfileReferencesProcessed = cleanupRefs;
    if (cleanupRefs < 0) { report.passed = false; report.error ??= 'Continuation credential cleanup failed.'; }
    const leakScan = scanArtifactsForSecret([continuationDir, evidenceRoot, tempRoot, path.join(project, 'scripts/verify-v060-live-cloud.cjs'), path.join(project, 'scripts/continue-v060-live-cloud.cjs')], args.keyFile);
    report.credential.leakScan = { checkedFiles: leakScan.checkedFiles, keyMatches: leakScan.matches };
    if (leakScan.matches > 0) { report.passed = false; report.error ??= 'Secret string found in continuation evidence.'; }
    report.profileId = createdProfileId;
    report.assistantThreadId = assistantThreadId;
    report.assistantRunId = assistantRunId;
    report.cloudCalls = readCalls(localLedgerPath).calls;
    report.cloudCallsBeforeContinuation = earlierDispatchCount;
    report.continuationCloudCalls = report.cloudCalls.slice(earlierDispatchCount);
    report.continuationEndpointCounts = {
      models: report.continuationCloudCalls.filter((item) => item.endpoint === 'models').length,
      chatCompletions: report.continuationCloudCalls.filter((item) => item.endpoint === 'chat/completions').length,
    };
    const budget = summarizeBudgetLedger(globalLedgerPath, globalHistoryPath);
    if (budget.error) { report.passed = false; report.error ??= 'Cumulative cloud budget ledger parsing failed.'; }
    const usedMetadata = budget.actual.models + budget.reserved.models;
    const usedCompletions = budget.actual.completions + budget.reserved.completions;
    report.globalBudget = {
      limits: { modelMetadata: metadataBudget, chatCompletions: 15 },
      actualDispatches: budget.actual,
      conservativeReservations: budget.reserved,
      usedIncludingReservations: { modelMetadata: usedMetadata, chatCompletions: usedCompletions },
      remaining: { modelMetadata: metadataBudget - usedMetadata, chatCompletions: 15 - usedCompletions },
      ledger: path.relative(project, globalLedgerPath).replaceAll('\\', '/'),
      history: path.relative(project, globalHistoryPath).replaceAll('\\', '/'),
    };
    const markerState = readInstrumentationMarkers(env.ZHIYI_CLOUD_AUDIT_MARKER);
    const markerEntries = markerState.entries;
    if (markerState.errors.length) {
      report.passed = false;
      report.error ??= 'One or more isolated instrumentation marker files could not be parsed.';
      report.instrumentationMarkerParseErrors = markerState.errors;
    }
    report.instrumentation = { hook: 'httpx.Client.send; no request/response bodies or headers recorded', processesMarked: markerEntries.length, serviceRolesConfirmed: [...new Set(markerEntries.filter((item) => item.patched === true).map((item) => item.role).filter((role) => role === 'api' || role === 'worker'))].sort() };
    fs.writeFileSync(path.join(continuationDir, 'continuation.json'), JSON.stringify(report, null, 2), { encoding: 'utf8', flag: 'w' });
    fs.writeFileSync(path.join(continuationDir, 'passed.json'), JSON.stringify({ passed: report.passed, continuationEndpointCounts: report.continuationEndpointCounts, globalBudget: report.globalBudget, credentialCleanup: report.credential.cleanup, keyMatches: leakScan.matches }), { encoding: 'utf8', flag: 'w' });
  }
  console.log(JSON.stringify({ passed: report.passed, continuation: path.relative(project, continuationDir).replaceAll('\\', '/'), endpointCounts: report.continuationEndpointCounts, error: report.error ?? null }));
  if (!report.passed) process.exitCode = 1;
}

runContinuation().catch((error) => {
  console.error(JSON.stringify({ passed: false, error: safeError(error) }));
  process.exitCode = 1;
});
