// Process lifecycle tests only: no process is launched or terminated.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadHelper(platform, onTaskkill) {
  const filename = path.resolve(__dirname, '../verify-v060-live-cloud.cjs');
  const module = { exports: {} };
  const localRequire = name => {
    if (name === 'node:child_process') return {
      spawn() { throw new Error('No subprocess may start in this test'); },
      spawnSync: onTaskkill,
    };
    if (name === 'playwright') return {};
    return require(name);
  };
  localRequire.main = {};
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, require: localRequire, __dirname: path.dirname(filename), __filename: filename,
    process: { platform, env: {} }, setTimeout, clearTimeout, Buffer,
  }, { filename });
  return module.exports.stopChild;
}

for (const state of [{ exitCode: 0, signalCode: null }, { exitCode: null, signalCode: 'SIGTERM' }]) {
  test(`an exited Windows child never reuses its former PID (${JSON.stringify(state)})`, async () => {
    const commands = [];
    const stopChild = loadHelper('win32', (...args) => commands.push(args));
    const child = { pid: 123456789, ...state, kill() { throw new Error('Already exited'); } };
    assert.equal(await stopChild(child), true);
    assert.equal(commands.length, 0);
  });
}

test('a live Windows child still receives the owned process-tree stop', async () => {
  const calls = [];
  const child = { pid: 123456789, exitCode: null, signalCode: null };
  const stopChild = loadHelper('win32', (command, args) => {
    calls.push([command, Array.from(args)]);
    child.exitCode = 1;
    return { status: 0 };
  });
  assert.equal(await stopChild(child), true);
  assert.deepEqual(calls, [['taskkill.exe', ['/PID', '123456789', '/T', '/F']]]);
});

test('an absent child needs no process command', async () => {
  const stopChild = loadHelper('win32', () => { throw new Error('Unexpected process command'); });
  assert.equal(await stopChild(null), true);
});
