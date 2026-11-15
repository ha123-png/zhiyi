// Live cloud + headless Chromium verification using only synthetic records.
// The key is read from an explicitly selected file into process memory, enters
// the password field over loopback, and is never included in evidence/logs.
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { randomUUID, createHash } = require('node:crypto');
const { chromium } = require('playwright');
const assert = require('node:assert/strict');

const project = path.resolve(__dirname, '..');
const python = process.env.ZHIYI_TEST_PYTHON || path.join(project, 'apps/api/.venv/Scripts/python.exe');
const API_SRC = path.join(project, 'apps/api/src');
const DIST = path.join(project, 'apps/web/dist');
const defaultBudget = 12;

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--key-file') args.keyFile = argv[++i];
    else if (argv[i] === '--call-budget') args.callBudget = Number(argv[++i]);
    else throw new Error('Use --key-file <path> and optionally --call-budget 10..15.');
  }
  if (!args.keyFile) throw new Error('An explicit --key-file is required.');
  args.callBudget ??= defaultBudget;
  if (!Number.isInteger(args.callBudget) || args.callBudget < 10 || args.callBudget > 15) {
    throw new Error('The real-model request budget must be between 10 and 15.');
  }
  args.keyFile = path.resolve(project, args.keyFile);
  return args;
}

function safeError(error, secret = '') {
  let value = String(error?.message ?? error ?? 'verification failed');
  if (secret) value = value.split(secret).join('[REDACTED]');
  return value
    .replace(/Bearer\s+\S+/gi, 'Bearer [REDACTED]')
    .replace(/(api[_-]?key\s*[:=]\s*)\S+/gi, '$1[REDACTED]')
    .slice(0, 500);
}

async function freePort() {
  const server = net.createServer();
  server.listen(0, '127.0.0.1');
  await new Promise((resolve, reject) => {
    server.once('listening', resolve);
    server.once('error', reject);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

function pythonInstrumentation(completionBudget, metadataBudget) {
  return String.raw`"""Per-run sanitized external completion counter, loaded via PYTHONPATH."""
import json, os, time
from pathlib import Path
import httpx

_ledger = Path(os.environ["ZHIYI_CLOUD_AUDIT_LEDGER"])
_lock_path = Path(str(_ledger) + ".lock")
_global_ledger = Path(os.environ["ZHIYI_CLOUD_AUDIT_GLOBAL_LEDGER"])
_global_lock_path = Path(str(_global_ledger) + ".lock")
_global_history = Path(os.environ["ZHIYI_CLOUD_AUDIT_GLOBAL_HISTORY"])
_marker = Path(os.environ["ZHIYI_CLOUD_AUDIT_MARKER"])
_run_id = os.environ["ZHIYI_CLOUD_AUDIT_RUN_ID"]
_completion_budget = int(os.environ["ZHIYI_CLOUD_AUDIT_COMPLETION_BUDGET"])
_metadata_budget = int(os.environ["ZHIYI_CLOUD_AUDIT_METADATA_BUDGET"])

def _write_record(ledger, record):
    with ledger.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())

def _locked_append(record, reserve=False):
    import msvcrt
    fd = os.open(_global_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        try:
            if reserve:
                counts = {"chat/completions": 0, "models": 0}
                if _global_ledger.exists():
                    for line in _global_ledger.read_text(encoding="utf-8").splitlines():
                        try:
                            old = json.loads(line)
                            if not isinstance(old, dict):
                                raise RuntimeError("Cloud budget ledger is corrupted; refusing remote model request.")
                            if old.get("event") == "dispatch" and old.get("endpoint") in counts:
                                counts[old["endpoint"]] += 1
                            elif old.get("event") == "baseline_reservation" and old.get("endpoint") in counts:
                                reservation = old.get("count")
                                if not isinstance(reservation, int) or reservation < 1:
                                    raise RuntimeError("Cloud budget ledger is corrupted; refusing remote model request.")
                                counts[old["endpoint"]] += reservation
                        except (ValueError, TypeError):
                            raise RuntimeError("Cloud budget ledger is corrupted; refusing remote model request.") from None
                if _global_history.exists():
                    try:
                        history = json.loads(_global_history.read_text(encoding="utf-8"))
                    except (ValueError, TypeError):
                        raise RuntimeError("Cloud budget history is corrupted; refusing remote model request.") from None
                    if (not isinstance(history, dict) or history.get("schema") != "zhiyi.v060.cloud-budget-history.v1"
                            or not isinstance(history.get("entries"), list)):
                        raise RuntimeError("Cloud budget history is corrupted; refusing remote model request.")
                    for entry in history["entries"]:
                        if (not isinstance(entry, dict) or entry.get("endpoint") not in counts
                                or not isinstance(entry.get("count"), int) or entry["count"] < 1):
                            raise RuntimeError("Cloud budget history is corrupted; refusing remote model request.")
                        counts[entry["endpoint"]] += entry["count"]
                endpoint = record.get("endpoint")
                endpoint_budget = _metadata_budget if endpoint == "models" else _completion_budget
                if counts.get(endpoint, 0) >= endpoint_budget:
                    raise RuntimeError("Live-cloud audit endpoint request budget reached.")
                record["call"] = sum(counts.values()) + 1
                record["endpoint_call"] = counts[endpoint] + 1
            record["run"] = _run_id
            _write_record(_global_ledger, record)
            _write_record(_ledger, record)
        finally:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)

_original_send = httpx.Client.send
def _audit_send(client, request, *args, **kwargs):
    host = (request.url.host or "").lower()
    path = request.url.path
    endpoint = "chat/completions" if path.endswith("/chat/completions") else ("models" if path.endswith("/models") else None)
    external_model_request = endpoint is not None and host not in {"localhost", "127.0.0.1", "::1"}
    if not external_model_request:
        return _original_send(client, request, *args, **kwargs)
    started = time.monotonic()
    item = {"event": "dispatch", "host": host, "endpoint": endpoint, "method": request.method, "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _locked_append(item, reserve=True)
    call = item["call"]
    try:
        response = _original_send(client, request, *args, **kwargs)
    except Exception as error:
        _locked_append({"event": "result", "call": call, "status": "transport_error", "error_type": type(error).__name__, "elapsed_seconds": round(time.monotonic() - started, 3)})
        raise
    _locked_append({"event": "result", "call": call, "status": "http_response", "http_status": response.status_code, "elapsed_seconds": round(time.monotonic() - started, 3)})
    return response

httpx.Client.send = _audit_send
_marker_record = {"pid": os.getpid(), "role": os.environ.get("ZHIYI_CLOUD_AUDIT_PROCESS_ROLE", "unassigned"), "hook": "httpx.Client.send", "patched": httpx.Client.send is _audit_send}
_marker_file = _marker.with_name(f"{_marker.stem}-{os.getpid()}.json")
_marker_file.write_text(json.dumps(_marker_record, separators=(",", ":")), encoding="utf-8")
`;
}

function apiBootstrapSource() {
  return `import httpx, sitecustomize, sys\nif httpx.Client.send is not sitecustomize._audit_send:\n    raise RuntimeError("HTTPX live-cloud audit hook did not attach to API process")\nimport uvicorn\nuvicorn.run("document_pipeline_api.main:app", host="127.0.0.1", port=int(sys.argv[1]), app_dir=sys.argv[2])\n`;
}

function startLogged(executable, args, options, logPath) {
  const log = fs.createWriteStream(logPath, { flags: 'a' });
  const child = spawn(executable, args, { ...options, windowsHide: true });
  child.stdout.pipe(log);
  child.stderr.pipe(log);
  child.on('exit', () => log.end());
  return child;
}

function waitForExit(child, milliseconds = 7000) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      child.off('exit', onExit);
      resolve(false);
    }, milliseconds);
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    child.once('exit', onExit);
  });
}

async function stopChild(child) {
  // A completed child no longer owns its PID; Windows may have reused it.
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  if (process.platform === 'win32' && Number.isInteger(child.pid)) {
    // Python venv launchers can own child processes; kill the isolated run's
    // complete process tree before treating the wrapper's exit as cleanup.
    spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { encoding: 'utf8', windowsHide: true });
    if (await waitForExit(child, 7000)) return true;
    child.kill();
    return waitForExit(child, 7000);
  }
  child.kill();
  if (await waitForExit(child)) return true;
  child.kill('SIGKILL');
  return waitForExit(child, 7000);
}

async function waitForReady(url, child, name) {
  for (let i = 0; i < 120; i += 1) {
    if (child.exitCode !== null) throw new Error(`${name} exited before becoming ready.`);
    try {
      const response = await fetch(`${url}/api/v1/templates`, { signal: AbortSignal.timeout(900) });
      if (response.ok) return;
    } catch { /* Retry startup only. */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`${name} readiness timed out.`);
}

async function waitForWorker(url, child, heartbeatPath) {
  // The status endpoint probes the configured model service as a side effect.
  // Polling it during startup can issue repeated unauthenticated remote calls,
  // so use the isolated worker's heartbeat file as the readiness signal.
  for (let i = 0; i < 120; i += 1) {
    if (child.exitCode !== null) throw new Error('Isolated worker exited before heartbeat became visible.');
    try {
      const data = JSON.parse(fs.readFileSync(heartbeatPath, 'utf8'));
      if (Number.isFinite(data.timestamp) && (Date.now() / 1000) - data.timestamp <= 8) return;
    } catch { /* Worker has not written the isolated heartbeat yet. */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('Isolated worker heartbeat did not become ready.');
}

async function waitForInstrumentation(markerPath, children) {
  for (let i = 0; i < 40; i += 1) {
    if (children.some((child) => child.exitCode !== null)) throw new Error('An isolated Python process exited before audit instrumentation was ready.');
    try {
      const { entries } = readInstrumentationMarkers(markerPath);
      const activeRoles = new Set(entries.filter((entry) => entry.patched === true).map((entry) => entry.role));
      if (activeRoles.has('api') && activeRoles.has('worker')) return activeRoles.size;
    } catch { /* Wait for both server and worker to import sitecustomize. */ }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error('HTTPX audit instrumentation did not attach to both isolated API and worker roles.');
}

function readInstrumentationMarkers(markerPath) {
  const directory = path.dirname(markerPath);
  const prefix = `${path.basename(markerPath, path.extname(markerPath))}-`;
  const entries = [];
  const errors = [];
  if (!fs.existsSync(directory)) return { entries, errors };
  for (const name of fs.readdirSync(directory).filter((item) => item.startsWith(prefix) && /^\d+\.json$/.test(item.slice(prefix.length)))) {
    try {
      const value = JSON.parse(fs.readFileSync(path.join(directory, name), 'utf8'));
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('marker is not an object');
      entries.push(value);
    } catch (error) {
      errors.push({ file: name, error: error?.message ?? 'invalid marker' });
    }
  }
  return { entries, errors };
}

function directoryFingerprint(root) {
  const hash = createHash('sha256');
  const visit = (directory) => {
    for (const item of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const absolute = path.join(directory, item.name);
      const relative = path.relative(root, absolute).replaceAll('\\\\', '/');
      if (item.isDirectory()) visit(absolute);
      else if (item.isFile()) {
        hash.update(relative).update('\\0').update(fs.readFileSync(absolute)).update('\\0');
      }
    }
  };
  visit(root);
  return hash.digest('hex');
}

function sourceFingerprint() {
  const inputs = [
    path.join(project, 'scripts/verify-v060-live-cloud.cjs'),
    path.join(project, 'apps/api/src/document_pipeline_api'),
    path.join(project, 'apps/web/src/components/ExtractPage.tsx'),
    path.join(project, 'apps/web/src/assistant/AssistantSurface.tsx'),
    path.join(project, 'apps/web/src/assistant/AssistantProvider.tsx'),
  ];
  const hash = createHash('sha256');
  for (const input of inputs) {
    if (!fs.existsSync(input)) continue;
    const stat = fs.statSync(input);
    if (stat.isDirectory()) {
      const nested = directoryFingerprint(input);
      hash.update(path.relative(project, input)).update('\\0').update(nested).update('\\0');
    } else {
      hash.update(path.relative(project, input)).update('\\0').update(fs.readFileSync(input)).update('\\0');
    }
  }
  return hash.digest('hex');
}

function scanArtifactsForSecret(roots, keyFile) {
  const secret = Buffer.from(fs.readFileSync(keyFile, 'utf8').replace(/^\uFEFF/, '').trim(), 'utf8');
  let checked = 0;
  let matches = 0;
  const visit = (file) => {
    const stat = fs.statSync(file);
    if (stat.isDirectory()) {
      for (const item of fs.readdirSync(file)) visit(path.join(file, item));
      return;
    }
    if (!stat.isFile()) return;
    checked += 1;
    if (secret.length > 0 && fs.readFileSync(file).includes(secret)) matches += 1;
  };
  try {
    for (const root of roots) if (fs.existsSync(root)) visit(root);
  } finally {
    secret.fill(0);
  }
  return { checkedFiles: checked, matches };
}

function observeResponse(page, predicate, timeout = 30000) {
  return page.waitForResponse(predicate, { timeout }).then(
    (response) => ({ response }),
    (error) => ({ errorType: error?.name ?? 'response_timeout' }),
  );
}

function exportWorkbookFacts(file, env) {
  const code = String.raw`import json, sys
from openpyxl import load_workbook
book = load_workbook(sys.argv[1], data_only=True, read_only=True)
sheet = book.active
rows = list(sheet.iter_rows(values_only=True))
print(json.dumps({"sheet": sheet.title, "rows": rows}, ensure_ascii=False, default=str))
book.close()`;
  const parsed = spawnSync(python, ['-c', code, file], { cwd: project, env, encoding: 'utf8', windowsHide: true });
  if (parsed.status !== 0) throw new Error('Could not parse the downloaded synthetic XLSX.');
  return JSON.parse(parsed.stdout.trim());
}

function readCalls(ledgerPath) {
  if (!fs.existsSync(ledgerPath)) return { calls: [], error: null };
  try {
    const events = fs.readFileSync(ledgerPath, 'utf8').trim().split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    if (events.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) throw new Error('invalid row');
    return { calls: events.filter((item) => item.event === 'dispatch').map((dispatch) => ({
      call: dispatch.call,
      host: dispatch.host,
      endpoint: dispatch.endpoint,
      method: dispatch.method,
      result: events.find((item) => item.event === 'result' && item.call === dispatch.call) ?? null,
    })), error: null };
  } catch {
    return { calls: [], error: 'per-run cloud call ledger is unreadable' };
  }
}

function summarizeBudgetLedger(ledgerPath, historyPath) {
  const empty = { actual: { models: 0, completions: 0 }, reserved: { models: 0, completions: 0 } };
  let rows = [];
  if (fs.existsSync(ledgerPath)) {
    try { rows = fs.readFileSync(ledgerPath, 'utf8').split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line)); }
    catch { return { ...empty, error: 'global cloud budget ledger is unreadable' }; }
    if (rows.some((item) => !item || typeof item !== 'object' || Array.isArray(item))) return { ...empty, error: 'global cloud budget ledger is unreadable' };
  }
  let historyEntries = [];
  if (fs.existsSync(historyPath)) {
    try {
      const history = JSON.parse(fs.readFileSync(historyPath, 'utf8'));
      if (history.schema !== 'zhiyi.v060.cloud-budget-history.v1' || !Array.isArray(history.entries)
        || history.entries.some((entry) => !entry || typeof entry !== 'object' || Array.isArray(entry)
          || !['models', 'chat/completions'].includes(entry.endpoint)
          || !Number.isSafeInteger(entry.count) || entry.count < 1)) throw new Error('invalid history');
      historyEntries = history.entries;
    } catch { return { ...empty, error: 'local cloud budget history is unreadable' }; }
  }
  const dispatches = rows.filter((item) => item.event === 'dispatch');
  const reservations = [...rows.filter((item) => item.event === 'baseline_reservation'), ...historyEntries];
  return {
    actual: {
      models: dispatches.filter((item) => item.endpoint === 'models').length,
      completions: dispatches.filter((item) => item.endpoint === 'chat/completions').length,
    },
    reserved: {
      models: reservations.filter((item) => item.endpoint === 'models').reduce((total, item) => total + Number(item.count || 0), 0),
      completions: reservations.filter((item) => item.endpoint === 'chat/completions').reduce((total, item) => total + Number(item.count || 0), 0),
    },
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!fs.existsSync(args.keyFile)) throw new Error('The selected key file does not exist.');
  if (!fs.existsSync(python)) throw new Error('The API virtual environment is incomplete.');
  if (!fs.existsSync(path.join(DIST, 'index.html'))) throw new Error('Build the frontend before running the cloud browser verification.');

  const run = `${new Date().toISOString().replaceAll(':', '-')}-${randomUUID().slice(0, 8)}`;
  const evidenceRoot = path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-cloud', run);
  const tempRoot = path.join(project, '.local/tmp/2026-09-30-deep-cloud', run);
  const dataDir = path.join(tempRoot, 'data');
  const instrumentationDir = path.join(tempRoot, 'instrumentation');
  const frozenWebDir = path.join(tempRoot, 'web-dist');
  const profileName = `云端合成验收-${run.slice(-8)}`;
  const globalLedgerPath = path.join(project, '.local/evidence/v0.6.0/2026-09-30-deep-cloud/cloud-budget-ledger.jsonl');
  const globalHistoryPath = path.join(project, '.local/cloud-budget-history.json');
  const globalMetadataBudget = 200;
  const sourceEvidence = {
    gitHead: spawnSync('git', ['rev-parse', 'HEAD'], { cwd: project, encoding: 'utf8', windowsHide: true }).stdout.trim(),
    sourceSha256: sourceFingerprint(),
    originalWebDistSha256: directoryFingerprint(DIST),
    sharedDistIndexMtime: fs.statSync(path.join(DIST, 'index.html')).mtime.toISOString(),
  };
  const ledgerPath = path.join(evidenceRoot, 'cloud-calls.jsonl');
  fs.mkdirSync(evidenceRoot, { recursive: true });
  fs.mkdirSync(dataDir, { recursive: true });
  fs.mkdirSync(instrumentationDir, { recursive: true });
  fs.mkdirSync(path.dirname(globalLedgerPath), { recursive: true });
  if (!fs.existsSync(globalLedgerPath)) {
    try { fs.writeFileSync(globalLedgerPath, '', { encoding: 'utf8', flag: 'wx' }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  let historicalReservations = [];
  if (fs.existsSync(globalHistoryPath)) {
    const history = JSON.parse(fs.readFileSync(globalHistoryPath, 'utf8'));
    if (history.schema !== 'zhiyi.v060.cloud-budget-history.v1'
      || history.max?.chatCompletions !== 15
      || history.max?.modelMetadata !== globalMetadataBudget
      || !Array.isArray(history.entries)) {
      throw new Error('Local cloud budget history is malformed or has different limits.');
    }
    historicalReservations = history.entries.map((entry) => {
      if (!['models', 'chat/completions'].includes(entry.endpoint)
        || !Number.isSafeInteger(entry.count) || entry.count < 1
        || typeof entry.run !== 'string' || typeof entry.basis !== 'string') {
        throw new Error('Local cloud budget history contains an invalid reservation.');
      }
      return { event: 'baseline_reservation', endpoint: entry.endpoint, count: entry.count, run: entry.run, basis: entry.basis };
    });
  }
  if (!fs.existsSync(`${globalLedgerPath}.lock`)) {
    try { fs.writeFileSync(`${globalLedgerPath}.lock`, '0', { encoding: 'ascii', flag: 'wx' }); } catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  fs.cpSync(DIST, frozenWebDir, { recursive: true, errorOnExist: true });
  sourceEvidence.frozenWebDistSha256 = directoryFingerprint(frozenWebDir);
  if (sourceEvidence.frozenWebDistSha256 !== sourceEvidence.originalWebDistSha256) {
    throw new Error('Frozen web build differs from the shared build captured at run start.');
  }
  fs.writeFileSync(path.join(instrumentationDir, 'sitecustomize.py'), pythonInstrumentation(args.callBudget, globalMetadataBudget), { encoding: 'utf8', flag: 'wx' });
  const apiBootstrapPath = path.join(instrumentationDir, 'api-bootstrap.py');
  fs.writeFileSync(apiBootstrapPath, apiBootstrapSource(), { encoding: 'utf8', flag: 'wx' });
  fs.writeFileSync(ledgerPath + '.lock', '0', { encoding: 'ascii', flag: 'wx' });

  const env = { ...process.env, PYTHONUTF8: '1' };
  for (const name of Object.keys(env)) {
    if (name.startsWith('DOCUMENT_PIPELINE_') || name.startsWith('ZHIYI_CLOUD_AUDIT_')) delete env[name];
  }
  env.DOCUMENT_PIPELINE_DATA_DIR = dataDir;
  env.DOCUMENT_PIPELINE_QUEUE_DB = path.join(dataDir, 'queue.db');
  env.DOCUMENT_PIPELINE_WORKER_HEARTBEAT = path.join(dataDir, 'worker-heartbeat.json');
  env.DOCUMENT_PIPELINE_WEB_DIR = frozenWebDir;
  env.DOCUMENT_PIPELINE_MODEL_PROVIDER = 'openai_compatible';
  env.DOCUMENT_PIPELINE_MODEL_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1';
  env.DOCUMENT_PIPELINE_MODEL_NAME = 'qwen3.6-flash';
  env.ZHIYI_CLOUD_AUDIT_LEDGER = ledgerPath;
  env.ZHIYI_CLOUD_AUDIT_GLOBAL_LEDGER = globalLedgerPath;
  env.ZHIYI_CLOUD_AUDIT_GLOBAL_HISTORY = globalHistoryPath;
  env.ZHIYI_CLOUD_AUDIT_MARKER = path.join(instrumentationDir, 'loaded.jsonl');
  env.ZHIYI_CLOUD_AUDIT_RUN_ID = run;
  env.ZHIYI_CLOUD_AUDIT_COMPLETION_BUDGET = String(args.callBudget);
  env.ZHIYI_CLOUD_AUDIT_METADATA_BUDGET = String(globalMetadataBudget);
  env.PYTHONPATH = [instrumentationDir, API_SRC, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
  delete env.DOCUMENT_PIPELINE_MODEL_API_KEY;
  delete env.ZHIYI_OFFLINE;
  delete env.ZHIYI_FAST;

  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const apiEnv = { ...env, ZHIYI_CLOUD_AUDIT_PROCESS_ROLE: 'api' };
  const workerEnv = { ...env, ZHIYI_CLOUD_AUDIT_PROCESS_ROLE: 'worker' };
  const api = startLogged(python, [apiBootstrapPath, String(port), API_SRC], { cwd: project, env: apiEnv, stdio: ['ignore', 'pipe', 'pipe'] }, path.join(tempRoot, 'api.log'));
  const worker = startLogged(python, ['-m', 'huey.bin.huey_consumer', 'document_pipeline_api.worker.huey', '-w', '1', '-k', 'thread', '-d', '0.01', '-m', '0.01', '-b', '1'], { cwd: project, env: workerEnv, stdio: ['ignore', 'pipe', 'pipe'] }, path.join(tempRoot, 'worker.log'));
  let browser;
  let page;
  let modelProfileHttpEvents = [];
  let keyBuffer;
  let keyText = '';
  let redactionSecret = '';
  let keyEntered = false;
  const report = {
    schema: 'zhiyi.v060.live-cloud-audit.v1',
    run,
    passed: false,
    evidenceRoot: path.relative(project, evidenceRoot).replaceAll('\\', '/'),
    runtimeRoot: path.relative(project, tempRoot).replaceAll('\\', '/'),
    source: {
      gitHead: sourceEvidence.gitHead,
      sourceSha256: sourceEvidence.sourceSha256,
      originalWebDistSha256: sourceEvidence.originalWebDistSha256,
      frozenWebDistSha256: sourceEvidence.frozenWebDistSha256,
      frozenWebDir: path.relative(project, frozenWebDir).replaceAll('\\', '/'),
      sharedDistIndexMtime: sourceEvidence.sharedDistIndexMtime,
      provenance: 'source and web-dist hashes are independent observations; this run does not assert that the frozen web-dist was built from the recorded source snapshot',
    },
    browser: { engine: 'Playwright Chromium', headless: true, trace: false, har: false, videos: false },
    model: { provider: '阿里云百炼 OpenAI 兼容接口', model: 'qwen3.6-flash', realDataSent: '合成文本/表行', requestBudgets: { chatCompletions: args.callBudget, modelMetadata: globalMetadataBudget } },
    scenarios: [],
    secret: { inputType: 'password', screenshotsWhileKeyPresent: false, traceOrHarWhileKeyPresent: false, cleanup: 'pending' },
  };
  let createdProfileId = null;
  let createdTemplateId = null;
  let taskId = null;
  let assistantThreadId = null;
  let assistantRunId = null;
  let cleanupSecrets = 0;
  let failure = null;

  try {
    await waitForReady(baseUrl, api, 'Isolated API');
    await waitForWorker(baseUrl, worker, env.DOCUMENT_PIPELINE_WORKER_HEARTBEAT);
    report.instrumentationReadyProcesses = await waitForInstrumentation(env.ZHIYI_CLOUD_AUDIT_MARKER, [api, worker]);
    const onboardingSeed = await fetch(`${baseUrl}/api/v1/user-state/onboarding`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ completed_version: 999 }),
    });
    if (!onboardingSeed.ok) throw new Error(`Isolated onboarding precondition returned HTTP ${onboardingSeed.status}.`);
    report.preconditions = { onboardingMarkedCompleteInIsolatedRun: true, via: 'local API PUT before browser launch' };
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1366, height: 900 }, acceptDownloads: true });
    page = await context.newPage();
    const sseAttempts = [];
    const browserThreadRefreshes = [];
    modelProfileHttpEvents = [];
    page.on('request', (request) => {
      if (request.url().includes('/api/v1/models/profiles') && request.method() === 'POST') {
        modelProfileHttpEvents.push({ event: 'request', method: request.method(), route: new URL(request.url()).pathname });
      }
    });
    page.on('response', (response) => {
      if (response.url().includes('/api/v1/models/profiles') && response.request().method() === 'POST') {
        modelProfileHttpEvents.push({ event: 'response', method: response.request().method(), route: new URL(response.url()).pathname, status: response.status() });
      }
    });
    page.on('requestfailed', (request) => {
      if (request.url().includes('/api/v1/models/profiles') && request.method() === 'POST') {
        modelProfileHttpEvents.push({ event: 'requestfailed', method: request.method(), route: new URL(request.url()).pathname });
      }
    });
    let injectedFirstSseReset = false;
    page.on('response', (response) => {
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

    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });

    const templateName = '合成物资验收模板';
    const template = await apiRequest('POST', '/templates', {
      name: templateName,
      fields: [
        { key: 'doc_id', label: '单据编号', section: 'header', value_type: 'text' },
        { key: 'operator', label: '经办人', section: 'header', value_type: 'text' },
        { key: 'item', label: '物品', section: 'item', value_type: 'text' },
        { key: 'qty', label: '数量', section: 'item', value_type: 'number' },
        { key: 'amount', label: '金额', section: 'item', value_type: 'number' },
      ],
    });
    createdTemplateId = template.id;
    report.synthetic = { templateId: template.id, templateName, facts: 1, fields: 5 };

    if (!keyEntered) await page.screenshot({ path: path.join(evidenceRoot, 'cloud-preprofile-ui.png') });
    const profilesLoaded = observeResponse(page, (response) => response.url().endsWith('/api/v1/models/profiles') && response.request().method() === 'GET');
    await page.locator('.app-sidebar').getByRole('button', { name: '设置', exact: true }).click({ timeout: 10000 });
    const profilesLoadResult = await profilesLoaded;
    if (!profilesLoadResult.response?.ok()) throw new Error(`Model profile list did not load successfully${profilesLoadResult.response ? ` (HTTP ${profilesLoadResult.response.status()})` : ''}.`);
    const initialProfiles = await profilesLoadResult.response.json();
    const defaultProfile = page.locator('.profile-list .profile-item').filter({ hasText: '本地 AI 方案' });
    await defaultProfile.waitFor({ state: 'visible' });
    report.profileListReady = { getStatus: profilesLoadResult.response.status(), visibleDefaultProfile: true, profileCount: initialProfiles.length };
    await page.locator('#settings-ai').scrollIntoViewIfNeeded();
    await page.getByRole('button', { name: '新建空白方案', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#model-profile-name')?.value === ''
      && document.querySelector('#model-profile-url')?.value === ''
      && document.querySelector('#model-profile-model')?.value === '');
    assert.equal(await page.getByRole('button', { name: '创建方案', exact: true }).count(), 1);
    await page.locator('#model-profile-name').fill(profileName);
    await page.locator('#model-profile-provider').selectOption('aliyun');
    await page.locator('#model-profile-url').fill('https://dashscope.aliyuncs.com/compatible-mode/v1');
    await page.locator('#model-profile-model').fill('qwen3.6-flash');
    await page.locator('#model-profile-reasoning').selectOption('none');
    report.profileDraftReady = {
      providerPreset: await page.locator('#model-profile-provider').inputValue(),
      endpointConfigured: (await page.locator('#model-profile-url').inputValue()) === 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      modelConfigured: (await page.locator('#model-profile-model').inputValue()) === 'qwen3.6-flash',
    };
    assert.equal(report.profileDraftReady.providerPreset, 'aliyun');
    assert(report.profileDraftReady.endpointConfigured && report.profileDraftReady.modelConfigured);
    const keyInput = page.locator('#model-profile-key');
    assert.equal(await keyInput.getAttribute('type'), 'password');
    keyBuffer = fs.readFileSync(args.keyFile);
    keyText = keyBuffer.toString('utf8').replace(/^\uFEFF/, '').trim();
    if (!keyText) throw new Error('The selected key file is empty.');
    redactionSecret = keyText;
    keyEntered = true;
    await keyInput.fill(keyText);
    assert.equal(await keyInput.getAttribute('type'), 'password');
    const createButton = page.getByRole('button', { name: '创建方案', exact: true });
    if (!(await createButton.isEnabled())) throw new Error('Cloud profile create button is disabled after completing its visible fields.');
    const profileCreate = observeResponse(page, (response) => response.url().endsWith('/api/v1/models/profiles') && response.request().method() === 'POST');
    await createButton.click();
    const profileCreateResult = await profileCreate;
    if (!profileCreateResult.response) {
      report.modelProfileHttpEvents = modelProfileHttpEvents;
      throw new Error(`Cloud profile save produced no response (${profileCreateResult.errorType ?? 'unknown'}).`);
    }
    const createdResponse = profileCreateResult.response;
    if (!createdResponse.ok()) throw new Error(`Cloud profile creation returned HTTP ${createdResponse.status()}.`);
    const profileResponse = await createdResponse.json();
    createdProfileId = profileResponse.id;
    report.modelProfileHttpEvents = modelProfileHttpEvents;
    await page.getByText('方案已创建，请确认后激活。', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('#model-profile-key')?.value === '', null, { timeout: 10000 });
    keyBuffer.fill(0);
    keyBuffer = null;
    keyText = '';
    assert.equal(await keyInput.inputValue(), '');
    const profileList = await apiRequest('GET', '/models/profiles');
    const savedProfile = profileList.find((item) => item.id === createdProfileId);
    assert(savedProfile && savedProfile.has_api_key && savedProfile.is_remote);
    await page.getByRole('button', { name: '激活方案', exact: true }).click();
    const transferDialog = page.getByRole('dialog', { name: '确认使用云端模型？' });
    await transferDialog.waitFor();
    await transferDialog.getByRole('button', { name: '我确认，使用云端服务' }).click();
    await page.getByText('已激活；之后新上传的文件会使用这套方案。', { exact: true }).waitFor();
    const activeProfiles = await apiRequest('GET', '/models/profiles');
    const activeProfile = activeProfiles.find((item) => item.id === createdProfileId);
    assert(activeProfile?.is_active && activeProfile.is_remote);
    report.profileActivation = {
      active: activeProfile.is_active,
      remote: activeProfile.is_remote,
      confirmationDialogAcceptedInUI: true,
      profileAcknowledgementField: activeProfile.remote_data_acknowledged,
      acknowledgementNote: 'Activation confirmation is accepted per activation and is not persisted as a profile-wide acknowledgement.',
    };
    await page.screenshot({ path: path.join(evidenceRoot, 'cloud-profile-active.png') });
    report.scenarios.push({ id: 'model-profile-ui', result: 'passed', actions: ['create cloud profile in UI', 'select provider/model', 'save key through password field', 'confirm remote transfer and activate'], secretClearedFromForm: true });

    await page.locator('.app-sidebar').getByRole('button', { name: '文件提取', exact: true }).click();
    await page.getByLabel('提取模板').selectOption(createdTemplateId);
    const syntheticText = [
      '合成验收材料；所有内容均为虚构测试数据。',
      '单据编号：SYN-2026-001',
      '经办人：合成演示员',
      '明细：',
      '物品 | 数量 | 金额',
      '蓝色标签 | 2 | 6.5',
    ].join('\n') + '\n';
    const syntheticBytes = Buffer.from(syntheticText, 'utf8');
    report.synthetic.sourceSha256 = createHash('sha256').update(syntheticBytes).digest('hex');
    report.synthetic.sourceBytes = syntheticBytes.byteLength;
    const uploadResponsePromise = observeResponse(page, (response) => response.url().endsWith('/api/v1/tasks') && response.request().method() === 'POST');
    await page.locator('input[type="file"]').setInputFiles({ name: 'synthetic-materials-v060.txt', mimeType: 'text/plain', buffer: syntheticBytes });
    const uploadCapture = await uploadResponsePromise;
    if (!uploadCapture.response) throw new Error(`Synthetic document upload produced no response (${uploadCapture.errorType ?? 'unknown'}).`);
    const uploadResponse = uploadCapture.response;
    if (!uploadResponse.ok()) throw new Error(`Synthetic document upload returned HTTP ${uploadResponse.status()}.`);
    const upload = await uploadResponse.json();
    taskId = upload.id;
    const expected = {
      header: { doc_id: 'SYN-2026-001', operator: '合成演示员' },
      items: [{ item: '蓝色标签', qty: 2, amount: 6.5 }],
    };
    let initialExtraction = null;
    let terminalTaskStatus = null;
    const waitStart = Date.now();
    while (Date.now() - waitStart < 180_000) {
      const tasks = await apiRequest('GET', '/tasks');
      const currentTask = tasks.find((item) => item.id === taskId);
      if (currentTask?.status === 'failed') throw new Error(`Synthetic extraction failed (${currentTask.failure_code ?? 'no-code'}).`);
      if (currentTask && ['completed', 'needs_review'].includes(currentTask.status)) {
        terminalTaskStatus = currentTask.status;
        try {
          initialExtraction = await apiRequest('GET', `/tasks/${taskId}/result`);
          break;
        } catch { /* The completed extraction read can trail the task event briefly. */ }
      }
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    if (!initialExtraction) throw new Error('Real cloud extraction did not complete within three minutes.');
    const resultBody = initialExtraction.result;
    const mismatchFields = [];
    for (const [key, value] of Object.entries(expected.header)) if (resultBody?.header?.[key] !== value) mismatchFields.push(key);
    const actualItems = resultBody?.items ?? [];
    if (actualItems.length !== 1) mismatchFields.push('items.count');
    else for (const [key, value] of Object.entries(expected.items[0])) if (actualItems[0]?.[key] !== value) mismatchFields.push(`items[0].${key}`);
    const modelExact = mismatchFields.length === 0;
    const preReviewFields = {};
    for (const key of Object.keys(expected.header)) preReviewFields[key] = resultBody?.header?.[key] ?? null;
    if (actualItems.length === 1) for (const key of Object.keys(expected.items[0])) preReviewFields[key] = actualItems[0]?.[key] ?? null;
    report.extraction = { modelExactBeforeReview: modelExact, mismatchFields, preReviewFields, terminalStatus: terminalTaskStatus, templateId: createdTemplateId, inputCoverage: initialExtraction.input_scope?.coverage ?? null, issueCount: initialExtraction.validation_issues?.length ?? null, manualCorrectionApplied: !modelExact };

    async function setHeader(label, value) {
      const field = page.getByRole('button', { name: `编辑字段 ${label}` });
      await field.click();
      const editor = field.locator('[contenteditable="true"]');
      await editor.fill(value);
      await editor.press('Enter');
    }
    async function setItem(label, oldValue, value) {
      const cell = page.locator(`td[aria-label^="编辑 ${label}，当前值 ${String(oldValue || '空')}"]`).first();
      await cell.click();
      const editor = cell.locator('[contenteditable="true"]');
      await editor.fill(String(value));
      await editor.press('Enter');
    }
    if (resultBody?.items?.length === 1) {
      for (const [key, value] of Object.entries(expected.header)) if (resultBody.header?.[key] !== value) await setHeader(key === 'doc_id' ? '单据编号' : '经办人', value);
      for (const [key, value] of Object.entries(expected.items[0])) if (resultBody.items[0]?.[key] !== value) {
        const label = { item: '物品', qty: '数量', amount: '金额' }[key];
        const oldValue = resultBody.items[0]?.[key] == null ? '' : String(resultBody.items[0][key]);
        await setItem(label, oldValue, value);
      }
    }
    await page.screenshot({ path: path.join(evidenceRoot, 'cloud-extraction-review.png') });
    const reviewResponsePromise = page.waitForResponse((response) => response.url().endsWith(`/api/v1/tasks/${taskId}/review`) && response.request().method() === 'PUT');
    await page.getByRole('button', { name: '修订到表', exact: true }).click();
    const reviewResponse = await reviewResponsePromise;
    if (!reviewResponse.ok()) throw new Error(`Review save returned HTTP ${reviewResponse.status()}.`);
    const reviewedExtraction = await apiRequest('GET', `/tasks/${taskId}/result`);
    assert.deepEqual(reviewedExtraction.result, expected, 'Saved reviewed facts do not match the exact synthetic golden.');
    const confirmation = await apiRequest('GET', `/tasks/${taskId}/confirmation`);
    assert.equal(confirmation.task_id, taskId);
    assert.equal(confirmation.row_count, 1);
    assert.equal(typeof confirmation.confirmed_at, 'string');
    const table = await apiRequest('GET', `/tables/${confirmation.table_id}`);
    assert.equal(table.rows.length, 1);
    const projectedGolden = { ...expected.header, ...expected.items[0] };
    for (const [key, value] of Object.entries(projectedGolden)) {
      assert.equal(table.rows[0].values[key], value, `Current table projection differs from the golden field ${key}.`);
    }

    await page.locator('.app-sidebar').getByRole('button', { name: '数据仓库', exact: true }).click();
    await page.locator('.sheet-tree-item').filter({ hasText: templateName }).click();
    await page.getByRole('button', { name: '导出', exact: true }).click();
    const downloadPromise = page.waitForEvent('download').then((download) => ({ download }), (error) => ({ errorType: error?.name ?? 'download_failed' }));
    await page.getByRole('button', { name: /导出 Excel/ }).click();
    const downloadCapture = await downloadPromise;
    if (!downloadCapture.download) throw new Error(`XLSX download event was not observed (${downloadCapture.errorType ?? 'unknown'}).`);
    const download = downloadCapture.download;
    const exportPath = path.join(evidenceRoot, 'synthetic-reviewed-golden.xlsx');
    await download.saveAs(exportPath);
    const exportFacts = exportWorkbookFacts(exportPath, env);
    assert(exportFacts.rows.length >= 2, 'Downloaded workbook has no data row.');
    const headers = exportFacts.rows[0].map((value) => String(value ?? ''));
    const exportedRow = exportFacts.rows[1];
    const goldenByLabel = {
      单据编号: expected.header.doc_id,
      经办人: expected.header.operator,
      物品: expected.items[0].item,
      数量: expected.items[0].qty,
      金额: expected.items[0].amount,
    };
    const exportChecks = {};
    for (const [label, value] of Object.entries(goldenByLabel)) {
      const index = headers.indexOf(label);
      assert(index >= 0, `Downloaded Excel is missing the ${label} column.`);
      assert.equal(exportedRow[index], value, `Downloaded Excel ${label} differs from the exact golden.`);
      exportChecks[label] = exportedRow[index];
    }
    report.extraction = { ...report.extraction, reviewedExact: true, confirmed: true, tableId: confirmation.table_id, exportedExact: true, exportSha256: createHash('sha256').update(fs.readFileSync(exportPath)).digest('hex'), exportBytes: fs.statSync(exportPath).size, exportSheet: exportFacts.sheet, exportColumns: headers, exportFacts: exportChecks };
    await page.screenshot({ path: path.join(evidenceRoot, 'cloud-export-table.png') });
    report.scenarios.push({ id: 'extract-review-export', result: 'passed', actions: ['manual template selection in browser', 'upload through browser file input', 'real worker extraction using cloud model', 'review and save through browser', 'confirm current-data row and read its export menu', 'download actual XLSX and compare exact golden'] });

    await page.locator('.app-sidebar').getByRole('button', { name: '问知意', exact: true }).click();
    await page.locator('.ask-settings-trigger').click();
    const chatProfile = page.getByLabel('问知意模型方案');
    const refreshedChatProfiles = observeResponse(page, (response) => response.url().endsWith('/api/v1/models/profiles') && response.request().method() === 'GET');
    await chatProfile.focus();
    const refreshedChatProfilesResult = await refreshedChatProfiles;
    if (!refreshedChatProfilesResult.response?.ok()) throw new Error('Assistant cloud-profile options did not refresh after focusing the model selector.');
    const refreshedChatProfilesBody = await refreshedChatProfilesResult.response.json();
    assert(refreshedChatProfilesBody.some((item) => item.id === createdProfileId), 'Assistant cloud-profile refresh omitted the created profile.');
    await chatProfile.locator(`option[value="${createdProfileId}"]`).waitFor({ state: 'attached' });
    await chatProfile.selectOption(createdProfileId);
    await page.getByRole('tab', { name: '数据表' }).click();
    const searchResources = page.getByLabel('搜索可选资料');
    await searchResources.fill(templateName);
    const tableResource = page.locator('.ask-resource-list label').filter({ hasText: templateName });
    await tableResource.waitFor();
    await tableResource.locator('input[type="checkbox"]').check();
    await page.getByRole('button', { name: '只读分析', exact: true }).click();
    await page.getByRole('button', { name: '完成', exact: true }).click();
    const consent = page.locator('.ask-remote-consent input[type="checkbox"]');
    await consent.click();
    await consent.waitFor({ state: 'detached' });
    report.remoteConsentGrantedThroughUi = { checkboxClicked: true, componentDetachedAfterGrant: true };
    await page.route('**/api/v1/assistant/runs/*/events*', async (route) => {
      if (!injectedFirstSseReset) {
        injectedFirstSseReset = true;
        sseAttempts.push({ injected: true, status: 'connection_reset' });
        await route.abort('connectionreset');
      } else {
        await route.continue();
      }
    });
    const assistantQuestion = '请调用只读工具 read_data_rows，读取当前授权表的第一条记录和业务字段。根据工具结果简洁报告该记录的单据编号、经办人、物品、数量和金额，并说明来源表名；不要修改任何内容，也不要猜测。';
    await page.getByLabel('向问知意提问').fill(assistantQuestion);
    const assistantSendButton = page.getByRole('button', { name: '发送问题', exact: true });
    assert(await assistantSendButton.isEnabled(), 'The assistant send button is disabled after model, read-only scope, and remote-consent selection.');
    const assistantStartPromise = observeResponse(page, (response) => response.url().endsWith('/api/v1/assistant/runs') && response.request().method() === 'POST');
    await assistantSendButton.click();
    const assistantStartCapture = await assistantStartPromise;
    if (!assistantStartCapture.response) throw new Error(`Assistant start produced no response (${assistantStartCapture.errorType ?? 'unknown'}).`);
    const assistantStart = assistantStartCapture.response;
    if (!assistantStart.ok()) throw new Error(`Assistant start returned HTTP ${assistantStart.status()}.`);
    const started = await assistantStart.json();
    assistantThreadId = started.thread_id;
    assistantRunId = started.run_id;
    const assistantStartedAt = Date.now();
    let assistantDetail = null;
    while (Date.now() - assistantStartedAt < 180_000) {
      assistantDetail = await apiRequest('GET', `/assistant/threads/${assistantThreadId}?run_id=${assistantRunId}`);
      const runDetail = assistantDetail.runs?.find((item) => item.id === assistantRunId);
      if (runDetail && !['running', 'waiting', 'cancelling'].includes(runDetail.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 800));
    }
    const completedRun = assistantDetail?.runs?.find((item) => item.id === assistantRunId);
    assert.equal(completedRun?.status, 'completed', 'Live assistant read-only tool run did not complete.');
    const toolParts = (assistantDetail.messages ?? []).flatMap((message) => message.parts ?? []).filter((part) => part.type === 'tool');
    const toolNames = toolParts.map((item) => ({ name: item.name, status: item.status }));
    assert(toolNames.some((item) => item.name === 'read_data_rows' && item.status === 'completed'), 'The live assistant did not complete read_data_rows.');
    const readRowsTool = toolParts.find((item) => item.name === 'read_data_rows' && item.status === 'completed');
    const readRows = readRowsTool?.result?.rows;
    assert(Array.isArray(readRows) && readRows.length >= 1, 'The completed read_data_rows tool did not return a row.');
    const toolValues = readRows[0]?.values ?? {};
    const toolGolden = { ...expected.header, ...expected.items[0] };
    for (const [key, value] of Object.entries(toolGolden)) {
      assert.equal(toolValues[key], value, `Completed read_data_rows result differs from the synthetic golden field ${key}.`);
    }
    const usage = completedRun.usage ?? {};
    assert(Array.isArray(usage.calls) && usage.calls.length >= 1, 'Provider usage ledger is missing for the live assistant run.');
    assert(usage.calls.every((call) => typeof call.status === 'string'));
    const usageTokenKeys = ['prompt_tokens', 'completion_tokens', 'total_tokens'];
    for (const call of usage.calls) for (const key of usageTokenKeys) {
      if (key in call) assert(Number.isSafeInteger(call[key]) && call[key] >= 0, `Provider ${key} for call ${call.index} is invalid.`);
    }
    const callsWithUsage = usage.calls.filter((call) => Number.isSafeInteger(call.prompt_tokens) && call.prompt_tokens >= 0).length;
    assert.equal(usage.model_calls, usage.calls.length);
    assert.equal(usage.calls_with_usage, callsWithUsage);
    assert.equal(usage.usage_complete, callsWithUsage === usage.calls.length);
    const usageTotals = {};
    for (const key of usageTokenKeys) {
      const supplied = usage.calls.filter((call) => Number.isSafeInteger(call[key]) && call[key] >= 0);
      if (supplied.length) {
        usageTotals[key] = supplied.reduce((sum, call) => sum + call[key], 0);
        assert.equal(usage[key], usageTotals[key], `Persisted provider ${key} total differs from per-call values.`);
      } else {
        assert(usage[key] == null, `Persisted provider ${key} total has no per-call source.`);
        usageTotals[key] = null;
      }
    }
    await page.getByText(assistantQuestion, { exact: true }).waitFor();
    const threadTitle = assistantDetail.title;
    const assistantText = (assistantDetail.messages ?? []).filter((message) => message.role === 'assistant')
      .flatMap((message) => message.parts ?? []).filter((part) => part.type === 'text' && typeof part.text === 'string').map((part) => part.text).join('\n');
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
    assert(Object.values(assistantTextChecks).every(Boolean), 'Assistant text parts did not independently report all requested synthetic facts.');
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
    const assistantComposer = page.getByLabel('向问知意提问');
    await assistantComposer.fill('仅检查可发送状态，不发送。');
    const sendEnabledAfterAnswer = await assistantSendButton.isEnabled();
    await assistantComposer.fill('');
    const browserRefreshResults = await Promise.all(browserThreadRefreshes);
    const onErrorCompletedRefresh = browserRefreshResults.some((item) => item.status === 200 && item.runStatus === 'completed');
    const eventStream200Observed = sseAttempts.some((item) => item.contentType?.includes('text/event-stream') && item.status === 200);
    const recoveryMode = eventStream200Observed
      ? 'event-stream-http-200-observed'
      : onErrorCompletedRefresh && Object.values(browserTextChecks).every(Boolean) && sendEnabledAfterAnswer
        ? 'eventsource-onerror-http-completed-refresh-and-browser-ready'
        : 'unverified';
    assert(injectedFirstSseReset && recoveryMode !== 'unverified', 'Controlled SSE reset did not lead to an observed stream or HTTP-refresh/browser recovery.');
    assert(Object.values(browserTextChecks).every(Boolean), 'Browser-rendered assistant answer omitted a requested row fact.');
    assert(sendEnabledAfterAnswer, 'Assistant send button did not become available after the answer completed.');
    await page.screenshot({ path: path.join(evidenceRoot, 'cloud-assistant-tool-run.png') });
    report.assistant = {
      threadId: assistantThreadId,
      runId: assistantRunId,
      status: completedRun.status,
      remoteProfileId: completedRun.profile_id,
      toolCalls: toolNames,
      readOnlyToolResultExact: true,
      toolResultFields: toolGolden,
      usage: { providerCalls: usage.calls.length, callsWithUsage, usageComplete: usage.usage_complete, promptTokens: usageTotals.prompt_tokens, completionTokens: usageTotals.completion_tokens, totalTokens: usageTotals.total_tokens, tokenSource: callsWithUsage > 0 ? 'provider usage events persisted by tracked_stream' : 'unknown; provider did not report token usage' },
      sse: { resetInjected: injectedFirstSseReset, attempts: sseAttempts, eventStreamHttp200Observed: eventStream200Observed, browserThreadRefreshes: browserRefreshResults, onErrorCompletedRefresh, browserTextChecks, sendEnabledAfterAnswer, sendEnableCheckDraft: 'synthetic readiness probe; cleared without sending', recoveryMode },
      assistantTextChecks,
    };
    assert.equal(completedRun.profile_id, createdProfileId);
    await page.unroute('**/api/v1/assistant/runs/*/events*');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('.app-sidebar').getByRole('button', { name: '问知意', exact: true }).click();
    const historyTitle = page.locator('.ask-thread-title').filter({ hasText: threadTitle });
    await historyTitle.waitFor();
    await historyTitle.click();
    await page.getByText(assistantQuestion, { exact: true }).waitFor();
    const reloadedThread = await apiRequest('GET', `/assistant/threads/${assistantThreadId}`);
    const reloadedRun = reloadedThread.runs.find((item) => item.id === assistantRunId);
    assert.equal(reloadedRun.status, 'completed');
    assert.deepEqual(reloadedRun.usage, completedRun.usage, 'Assistant provider usage did not persist exactly after history reload.');
    const persistedReadRows = reloadedThread.tools.filter((item) => item.name === 'read_data_rows' && item.status === 'completed');
    assert.equal(persistedReadRows.length, 1);
    for (const [key, value] of Object.entries(toolGolden)) assert.equal(persistedReadRows[0].result?.rows?.[0]?.values?.[key], value);
    report.assistant.historyReload = { threadReopenedInUI: true, messagesPersisted: reloadedThread.messages.length, toolRecordPersisted: true, toolResultExactPersisted: true, usagePersisted: true, usageDeepEqualAfterReload: true };
    report.scenarios.push({ id: 'assistant-sse-tool-history-usage', result: 'passed', actions: ['select cloud chat model in UI', 'select the single synthetic table and read-only mode', 'acknowledge remote transfer for this conversation', 'use real cloud tool call with first event stream reset injected', `recover through ${recoveryMode} and render the answer`, 'reload browser and reopen persisted history; verify tool and provider usage'] });

    report.passed = true;
  } catch (error) {
    failure = safeError(error, redactionSecret || keyText);
    report.error = failure;
    if (page && !keyEntered) {
      try {
        report.preKeyDiagnostics = {
          url: page.url(),
          visibleDialogs: await page.locator('[role="dialog"]').evaluateAll((items) => items.filter((item) => item.getClientRects().length).map((item) => item.getAttribute('aria-label') ?? item.tagName)),
          skipGuideVisible: await page.getByRole('button', { name: '跳过引导' }).isVisible().catch(() => false),
        };
        await page.screenshot({ path: path.join(evidenceRoot, 'cloud-prekey-failure.png') });
      } catch { /* Preserve the original sanitized failure. */ }
    }
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
    if not refs:
        print(json.dumps({"deleted": 0}))
        raise SystemExit(0)
    store = create_model_secret_store()
    deleted = 0
    for ref in refs:
        store.delete(ref)
        deleted += 1
    print(json.dumps({"deleted": deleted}))
finally:
    engine.dispose()`;
    if (!workerExited || !apiExited) {
      cleanupSecrets = -1;
      report.secret.cleanup = 'failed because isolated API/worker did not exit';
      report.secret.cleanupFailureType = 'runtime_process_still_alive';
      report.passed = false;
    } else {
      const dbPath = path.join(dataDir, 'document-pipeline.db');
      const cleaned = spawnSync(python, ['-c', cleanupCode, dbPath, createdProfileId ?? '', profileName], { cwd: project, env, encoding: 'utf8', windowsHide: true });
      if (cleaned.status === 0 && !cleaned.error) {
        try {
          const cleanupResult = JSON.parse(cleaned.stdout.trim());
          cleanupSecrets = Number.isInteger(cleanupResult.deleted) ? cleanupResult.deleted : -1;
        } catch { cleanupSecrets = -1; }
      } else {
        cleanupSecrets = -1;
        report.secret.cleanup = 'failed; only this isolated run profile may require manual cleanup';
        report.secret.cleanupFailureType = cleaned.error?.name ?? 'cleanup_process_failed';
      }
    }
    report.secret.cleanup = cleanupSecrets >= 0 ? `deleted ${cleanupSecrets} credential reference(s) created by this run` : report.secret.cleanup;
    if (cleanupSecrets < 0) {
      report.passed = false;
      report.error ??= 'The run-scoped credential cleanup could not be verified.';
    }
    const parsedCalls = readCalls(ledgerPath);
    report.cloudCalls = parsedCalls.calls;
    if (parsedCalls.error) {
      report.passed = false;
      report.error ??= 'Per-run cloud call ledger parsing failed.';
      report.ledgerParseError = parsedCalls.error;
    }
    report.cloudCallCount = report.cloudCalls.length;
    report.modelProfileHttpEvents = modelProfileHttpEvents;
    report.endpointCounts = {
      modelMetadata: report.cloudCalls.filter((item) => item.endpoint === 'models').length,
      chatCompletions: report.cloudCalls.filter((item) => item.endpoint === 'chat/completions').length,
    };
    const budgetSummary = summarizeBudgetLedger(globalLedgerPath, globalHistoryPath);
    if (budgetSummary.error) {
      report.passed = false;
      report.error ??= 'Global cloud budget ledger parsing failed.';
      report.ledgerParseError = budgetSummary.error;
    }
    const modelMetadataUsed = budgetSummary.actual.models + budgetSummary.reserved.models;
    const chatCompletionsUsed = budgetSummary.actual.completions + budgetSummary.reserved.completions;
    report.globalBudget = {
      limits: { modelMetadata: globalMetadataBudget, chatCompletions: 15 },
      actualDispatches: budgetSummary.actual,
      conservativeReservationsForUntrackedRuns: budgetSummary.reserved,
      usedIncludingReservations: { modelMetadata: modelMetadataUsed, chatCompletions: chatCompletionsUsed },
      remaining: { modelMetadata: globalMetadataBudget - modelMetadataUsed, chatCompletions: 15 - chatCompletionsUsed },
      history: path.relative(project, globalHistoryPath).replaceAll('\\', '/'),
      ledger: path.relative(project, globalLedgerPath).replaceAll('\\', '/'),
    };
    const markerPath = path.join(instrumentationDir, 'loaded.jsonl');
    const markerState = readInstrumentationMarkers(markerPath);
    const markerEntries = markerState.entries;
    if (markerState.errors.length) {
      report.passed = false;
      report.error ??= 'One or more isolated instrumentation marker files could not be parsed.';
      report.instrumentationMarkerParseErrors = markerState.errors;
    }
    report.instrumentation = {
      httpxHookLoadedInProcesses: markerEntries.length,
      serviceRolesConfirmed: [...new Set(markerEntries.filter((item) => item.patched === true).map((item) => item.role).filter((role) => role === 'api' || role === 'worker'))].sort(),
      hook: 'httpx.Client.send; no request/response bodies or headers recorded',
    };
    report.credentialReferenceCreated = createdProfileId !== null;
    report.createdProfileId = createdProfileId;
    report.createdTemplateId = createdTemplateId;
    report.taskId = taskId;
    report.assistantThreadId = assistantThreadId;
    report.assistantRunId = assistantRunId;
    if (failure) report.passed = false;
    fs.writeFileSync(path.join(evidenceRoot, 'report.json'), JSON.stringify(report, null, 2), { encoding: 'utf8', flag: 'w' });
    const leakScan = scanArtifactsForSecret([evidenceRoot, tempRoot, path.join(project, 'scripts/verify-v060-live-cloud.cjs')], args.keyFile);
    report.secret.leakScan = { checkedFiles: leakScan.checkedFiles, keyMatches: leakScan.matches };
    if (leakScan.matches > 0) {
      report.passed = false;
      report.error ??= 'Secret string found in run artifacts; evidence requires manual containment.';
    }
    const safeReport = JSON.stringify(report, null, 2);
    fs.writeFileSync(path.join(evidenceRoot, 'report.json'), safeReport, { encoding: 'utf8', flag: 'w' });
    fs.writeFileSync(path.join(evidenceRoot, 'passed.json'), JSON.stringify({ passed: report.passed, cloudCallCount: report.cloudCallCount, endpointCounts: report.endpointCounts, globalBudget: report.globalBudget, secretCleanup: report.secret.cleanup, keyMatches: leakScan.matches }), { encoding: 'utf8', flag: 'w' });
  }
  console.log(JSON.stringify({ passed: report.passed, cloudCallCount: report.cloudCallCount, evidence: report.evidenceRoot, error: report.error ?? null }));
  if (!report.passed) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((error) => {
    console.error(JSON.stringify({ passed: false, error: safeError(error) }));
    process.exitCode = 1;
  });
}

module.exports = {
  directoryFingerprint,
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
  apiBootstrapSource,
};
