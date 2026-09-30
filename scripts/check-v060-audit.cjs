// Real browser + real isolated API. MCP transport and one settings 503 are injected.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const url = process.env.ZHIYI_ACCEPTANCE_URL;
  const output = process.env.ZHIYI_VISUAL_OUT;
  assert(url && output, 'Use scripts/verify-browser.cjs with its isolated server and output directory.');
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ ...(process.env.ZHIYI_BROWSER_CHANNEL ? { channel: process.env.ZHIYI_BROWSER_CHANNEL } : {}), headless: true });
  const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
  await page.context().tracing.start({ screenshots: true, snapshots: true });
  const report = { passed: false, realModelCalls: 0, scenarios: [] };
  const api = async (method, route, data) => {
    const response = await page.request.fetch(`${url}/api/v1${route}`, { method, data });
    assert(response.ok(), `${method} ${route}: ${response.status()} ${await response.text()}`);
    return response.status() === 204 ? null : response.json();
  };
  try {
    await page.goto(url);
    if (await page.getByRole('button', { name: '跳过引导' }).isVisible()) await page.getByRole('button', { name: '跳过引导' }).click();
    let configReads = 0;
    await page.route('**/integration/settings/mcp-config', async route => {
      configReads += 1;
      if (configReads === 1) await route.abort('connectionreset');
      else await route.continue();
    });
    await page.getByRole('button', { name: '接口', exact: true }).click();
    const configCard = page.locator('.card').filter({ has: page.getByRole('button', { name: /可直接使用的 MCP 配置/ }) });
    await configCard.getByRole('alert').waitFor();
    assert(!(await configCard.getByText('正在生成当前环境的 MCP 配置').isVisible()));
    await page.screenshot({ path: path.join(output, 'v060-mcp-read-error.png') });
    await configCard.getByRole('button', { name: /重新读取配置/ }).click();
    await configCard.getByText(/mcpServers/).waitFor();
    assert.equal(configReads, 2);
    await page.unroute('**/integration/settings/mcp-config');
    report.scenarios.push('MCP connection reset exits loading; retry reads real config without changing permissions');

    const backup = await api('POST', '/backups');
    await page.getByRole('button', { name: '备份', exact: true }).click();
    await page.getByRole('button', { name: /删除/ }).last().click();
    const dialog = page.getByRole('dialog', { name: '删除备份' });
    await dialog.waitFor();
    // The server really loses this synthetic backup between preview and commit.
    await api('DELETE', `/backups/${backup.name}`);
    await dialog.getByRole('button', { name: '确认删除' }).click();
    await dialog.getByRole('alert').waitFor();
    assert.match(await dialog.getByRole('alert').innerText(), /没有找到|不存在|移走/);
    assert(await dialog.getByRole('button', { name: '确认删除' }).isEnabled());
    await page.screenshot({ path: path.join(output, 'v060-backup-modal-error.png') });
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    report.scenarios.push('Real backup deletion race returns 404 inside the active accessible dialog');

    const oldSettings = await api('GET', '/system/settings');
    let settingsWrites = 0;
    await page.route('**/system/settings', async route => {
      if (route.request().method() !== 'PUT') return route.continue();
      settingsWrites += 1;
      if (settingsWrites === 1) return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '浏览器验收：首次保存失败' }) });
      return route.continue();
    });
    await page.getByRole('button', { name: '设置', exact: true }).click();
    const office = page.getByRole('button', { name: 'Office 文档自动转换', exact: true });
    await office.click();
    await page.getByText('浏览器验收：首次保存失败', { exact: true }).waitFor();
    assert.equal((await api('GET', '/system/settings')).office_convert, oldSettings.office_convert);
    assert.equal((await office.getAttribute('class')).includes(' on'), oldSettings.office_convert);
    const savedResponse = page.waitForResponse(response => response.url().endsWith('/system/settings') && response.request().method() === 'PUT' && response.ok());
    await office.click();
    await savedResponse;
    await page.getByText('浏览器验收：首次保存失败', { exact: true }).waitFor({ state: 'hidden' });
    assert.equal((await api('GET', '/system/settings')).office_convert, !oldSettings.office_convert);
    assert.equal(settingsWrites, 2);
    await page.screenshot({ path: path.join(output, 'v060-settings-retry.png') });
    await page.unroute('**/system/settings');
    await page.getByRole('button', { name: '数据仓库', exact: true }).click();
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await office.waitFor();
    await page.waitForFunction(expected => document.querySelector('[aria-label="Office 文档自动转换"]')?.classList.contains('on') === expected, !oldSettings.office_convert);
    report.scenarios.push('Settings 503 rolls back, real successful retry clears the old error and survives remount');

    const groupedName = 'v0.6分组范围浏览器验收';
    const groupedTemplate = await api('POST', '/templates', { name: groupedName, fields: [{ key: 'seller', label: '销售方', value_type: 'text', section: 'item' }] });
    const groupedTable = await api('POST', '/tables', { name: groupedName, template_key: groupedTemplate.id });
    const groupedRows = [];
    for (const seller of ['Alpha', 'Alpha', 'Beta', 'Beta']) groupedRows.push(await api('POST', `/tables/${groupedTable.id}/rows`, { values: { seller } }));
    const groups = await api('POST', `/tables/${groupedTable.id}/split`, { field_key: 'seller', group_by: 'field' });
    const alpha = groups.find(group => group.field_value === 'Alpha');
    assert(alpha);
    await api('PATCH', `/tables/${groupedTable.id}/rows/${groupedRows[0].id}`, { expected_version: groupedRows[0].version, changes: { seller: 'Gamma' } });
    await page.getByRole('button', { name: '数据仓库', exact: true }).click();
    await page.locator('.sheet-tree-item').filter({ hasText: groupedName }).click();
    await page.setViewportSize({ width: 799, height: 800 });
    const selector = page.getByLabel('选择数据表或分组');
    await selector.selectOption(`view:${alpha.id}`);
    const lastAlpha = page.getByRole('cell', { name: '编辑 销售方，当前值 Alpha', exact: true });
    await lastAlpha.click();
    await lastAlpha.locator('[contenteditable="true"]').fill('Gamma');
    await lastAlpha.locator('[contenteditable="true"]').press('Enter');
    await page.waitForFunction(id => document.querySelector('#compact-table')?.value === `table:${id}`, groupedTable.id);
    await page.getByRole('cell', { name: '编辑 销售方，当前值 Gamma', exact: true }).nth(1).waitFor();
    assert.equal(await page.getByRole('cell', { name: '编辑 销售方，当前值 Beta', exact: true }).count(), 2);
    assert.equal(await selector.locator(`option[value="view:${alpha.id}"]`).count(), 0);
    await page.getByRole('button', { name: '导出', exact: true }).click();
    const csvResponse = page.waitForResponse(response => response.url().includes(`/tables/${groupedTable.id}/export.csv`));
    const csvDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: /导出 CSV/ }).click();
    const exported = await csvResponse;
    assert(exported.ok());
    assert.equal(new URL(exported.url()).searchParams.get('view_id'), null);
    const csvPath = path.join(output, 'v060-group-scope.csv');
    await (await csvDownload).saveAs(csvPath);
    const csv = fs.readFileSync(csvPath, 'utf8');
    assert.equal((csv.match(/Gamma/g) || []).length, 2);
    assert.equal((csv.match(/Beta/g) || []).length, 2);
    await page.screenshot({ path: path.join(output, 'v060-group-scope.png') });
    const refreshedGroups = await api('GET', `/tables/${groupedTable.id}/views`);
    for (const value of ['Gamma', 'Beta']) {
      const group = refreshedGroups.find(item => item.field_value === value);
      assert(group);
      await selector.selectOption(`view:${group.id}`);
      await page.waitForFunction(value => document.querySelectorAll(`td[aria-label="编辑 销售方，当前值 ${value}"]`).length === 2, value);
    }
    await selector.selectOption(`table:${groupedTable.id}`);
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('button', { name: '数据仓库', exact: true }).click();
    await selector.selectOption(`table:${groupedTable.id}`);
    await page.getByRole('cell', { name: '编辑 销售方，当前值 Gamma', exact: true }).nth(1).waitFor();
    await page.setViewportSize({ width: 1366, height: 900 });
    report.scenarios.push('Last group member moves in a narrow real browser: selector, rows and downloaded CSV fall back together; Gamma/Beta and remount agree');

    const name = 'v0.6数值保真浏览器验收';
    const template = await api('POST', '/templates', { name, fields: [{ key: 'amount', label: '合计金额', value_type: 'number', section: 'item' }] });
    const table = await api('POST', '/tables', { name, template_key: template.id });
    const row = await api('POST', `/tables/${table.id}/rows`, { values: { amount: 372 } });
    await api('POST', `/tables/${table.id}/rows`, { values: { amount: '9007199254740993' } });
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('button', { name: '数据仓库', exact: true }).click();
    await page.locator('.sheet-tree-item').filter({ hasText: name }).click();
    await page.getByRole('cell', { name: '编辑 合计金额，当前值 9007199254740993', exact: true }).waitFor();
    const cell = page.getByRole('cell', { name: '编辑 合计金额，当前值 372', exact: true });
    await cell.click();
    const editor = cell.locator('[contenteditable="true"]');
    await editor.fill('1,23');
    await editor.press('Enter');
    await page.getByText(/未保存输入（1）· 展开查看或复制/).click();
    const draft = page.getByRole('textbox', { name: new RegExp(`^未保存输入：${name}.*合计金额$`) });
    await draft.waitFor();
    assert.equal(await draft.inputValue(), '1,23');
    const detail = await api('GET', `/tables/${table.id}`);
    assert.equal(detail.rows.find(item => item.id === row.id).values.amount, 372);
    assert(detail.rows.some(item => item.values.amount === '9007199254740993'));
    await page.screenshot({ path: path.join(output, 'v060-numeric-draft.png') });
    report.scenarios.push('Invalid numeric input keeps its draft and leaves real stored data intact; unsafe integer digits display exactly');
    report.passed = true;
  } catch (error) {
    report.error = String(error);
    await page.screenshot({ path: path.join(output, 'v060-failure.png') }).catch(() => {});
    throw error;
  } finally {
    fs.writeFileSync(path.join(output, 'v060-audit-report.json'), JSON.stringify(report, null, 2));
    await page.context().tracing.stop({ path: path.join(output, 'v060-audit-trace.zip') });
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
