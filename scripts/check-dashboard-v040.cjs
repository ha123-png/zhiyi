// Full-app browser journey against the explicitly supplied synthetic verification service.
// Preserves existing cards and removes only this run's uniquely named statistic.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');

(async () => {
  const url = process.env.ZHIYI_ACCEPTANCE_URL || 'http://127.0.0.1:8914';
  const out = path.resolve('.local/v040-dashboard-browser', new Date().toISOString().replaceAll(':', '-'));
  fs.mkdirSync(out, { recursive: true });
  const report = { url, realModelCalls: 0, cases: [], errors: [], preservedIds: [], createdId: null };
  const suffix = randomUUID().slice(0, 6);
  const firstName = `界面自动验收 · 供应方合计 ${suffix}`;
  const secondName = `界面自动验收 · 采购分布 ${suffix}`;
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: path.join(out, 'video'), size: { width: 1440, height: 1000 } } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  await context.addInitScript(() => { localStorage.setItem('onboarding-done-v4', '1'); localStorage.setItem('theme', 'light'); });
  const page = await context.newPage();
  page.on('pageerror', error => report.errors.push(error.message));
  const api = `${url}/api/v1`;
  const request = async suffix => {
    const response = await context.request.get(api + suffix);
    assert(response.ok(), `GET ${suffix}: ${response.status()}`);
    return response.json();
  };
  const initial = await request('/stats/cards');
  report.preservedIds = initial.items.map(item => item.id);
  assert(initial.items.length < initial.limit, 'Synthetic dashboard needs one free card slot');
  const options = await request('/stats/card-options');
  const source = options.tables.find(table => table.id === 'ask-synthetic');
  assert(source, 'Use only the scripted ask-synthetic table');
  let cardId;
  const openDashboard = async () => {
    await page.getByRole('button', { name: '仪表盘', exact: true }).click();
    await page.getByRole('heading', { name: '数据仪表盘', exact: true }).waitFor();
    await page.getByRole('button', { name: '添加统计', exact: true }).waitFor();
  };
  const save = async () => {
    const submit = page.getByRole('button', { name: '保存统计', exact: true });
    await submit.waitFor();
    await page.waitForFunction(() => !document.querySelector('.statistic-dialog button[type="submit"]')?.disabled);
    await submit.click();
    await page.locator('.statistic-dialog').waitFor({ state: 'detached' });
  };
  try {
    await page.goto(url);
    await openDashboard();
    await page.getByRole('button', { name: '添加统计', exact: true }).click();
    await page.getByRole('combobox', { name: '数据表', exact: true }).selectOption(source.id);
    await page.getByRole('combobox', { name: '统计什么', exact: true }).selectOption('sum');
    await page.getByRole('combobox', { name: '数值字段', exact: true }).selectOption('total');
    await page.getByRole('combobox', { name: '按什么分组', exact: true }).selectOption('vendor');
    await page.getByRole('combobox', { name: '展示方式', exact: true }).selectOption('donut');
    await page.getByRole('textbox', { name: '名称', exact: true }).fill(firstName);
    await save();
    let card = page.getByRole('article', { name: firstName, exact: true });
    await card.waitFor();
    await card.scrollIntoViewIfNeeded();
    const created = await request('/stats/cards');
    cardId = created.items.find(item => item.name === firstName)?.id;
    assert(cardId, 'Saved statistic must exist in persistent API');
    report.createdId = cardId;
    await page.waitForFunction(name => document.querySelector(`article[aria-label="${name}"] .dashboard-donut`), firstName);
    const result = await request(`/stats/cards/${cardId}/result?days=7`);
    assert.equal(result.status, 'ready');
    const metric = result.analysis.metric_keys[0];
    assert.equal(result.analysis.data.reduce((sum, row) => sum + row[metric], 0), 20800, 'Synthetic amounts total 20,800');
    await page.screenshot({ path: path.join(out, 'created-1440.png') });
    report.cases.push({ name: 'create and aggregate', cardId, total: 20800 });

    await card.getByRole('button', { name: `管理统计：${firstName}` }).click();
    await page.getByRole('menuitem', { name: '修改统计' }).click();
    await page.getByRole('textbox', { name: '名称', exact: true }).fill(secondName);
    await page.getByRole('combobox', { name: '展示方式', exact: true }).selectOption('bar');
    await save();
    card = page.getByRole('article', { name: secondName, exact: true });
    await card.waitFor();
    await page.reload();
    await openDashboard();
    await card.waitFor();
    await card.scrollIntoViewIfNeeded();
    const persisted = (await request('/stats/cards')).items.find(item => item.id === cardId);
    assert.equal(persisted.name, secondName);
    assert.equal(persisted.display, 'bar');
    await page.getByRole('button', { name: '刷新', exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.dashboard-toolbar button')?.disabled);
    await card.scrollIntoViewIfNeeded();
    report.cases.push({ name: 'rename, change display, reload and refresh', persistedName: persisted.name });

    await card.getByRole('button', { name: '查看原表' }).click();
    await page.getByRole('button', { name: new RegExp(source.name) }).first().waitFor();
    assert((await page.locator('.app-page-wrap').innerText()).includes(source.name), 'View original table navigates to the saved source');
    await page.screenshot({ path: path.join(out, 'source-table.png') });
    report.cases.push({ name: 'view original table', table: source.id });
    await openDashboard();

    for (const [width, dark, reduced] of [[1024, false, false], [400, false, false], [400, true, true], [1024, true, true]]) {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ reducedMotion: reduced ? 'reduce' : 'no-preference' });
      await page.evaluate(dark => document.documentElement.classList.toggle('dark', dark), dark);
      await card.scrollIntoViewIfNeeded();
      await page.waitForTimeout(320);
      const metrics = await page.evaluate(name => {
        const card = document.querySelector(`article[aria-label="${name}"]`);
        return { pageWidth: document.documentElement.scrollWidth, card: card.getBoundingClientRect().toJSON(), page: document.querySelector('.app-page-wrap').getBoundingClientRect().toJSON() };
      }, secondName);
      assert(metrics.pageWidth <= width + 1, `Page overflow at ${width}`);
      assert(metrics.card.left >= metrics.page.left - 1 && metrics.card.right <= width + 1, `Card overflow at ${width}`);
      await page.screenshot({ path: path.join(out, `card-${width}-${dark ? 'dark' : 'light'}-${reduced ? 'reduced' : 'motion'}.png`) });
      await card.getByRole('button', { name: `管理统计：${secondName}` }).click();
      await page.getByRole('menuitem', { name: '修改统计' }).click();
      const bounds = await page.locator('.statistic-dialog').boundingBox();
      assert(bounds.x >= -1 && bounds.x + bounds.width <= width + 1 && bounds.y >= -1 && bounds.y + bounds.height <= 901, 'Dialog stays inside viewport');
      const footer = page.locator('.statistic-dialog-footer');
      await page.waitForFunction(() => !document.querySelector('.statistic-dialog button[type="submit"]')?.disabled);
      if (width === 400) {
        await page.locator('.statistic-preview').scrollIntoViewIfNeeded();
        assert(await page.locator('.statistic-preview .dashboard-plot').isVisible(), 'Narrow editor preview remains reachable');
      }
      await footer.scrollIntoViewIfNeeded();
      assert(await page.getByRole('button', { name: '保存统计', exact: true }).isVisible());
      await page.screenshot({ path: path.join(out, `dialog-${width}-${dark ? 'dark' : 'light'}.png`) });
      await page.getByRole('button', { name: '关闭统计设置', exact: true }).click();
      report.cases.push({ name: 'card and editor responsive themes', width, dark, reduced, metrics });
    }
    assert.deepEqual(report.errors, []);
    report.passed = true;
  } finally {
    // Names are unique to this run. Never delete or rewrite any pre-existing card.
    const current = await request('/stats/cards');
    const own = current.items.filter(item => !report.preservedIds.includes(item.id) && [firstName, secondName].includes(item.name));
    for (const item of own) {
      const response = await context.request.delete(`${api}/stats/cards/${item.id}`);
      assert(response.ok(), `Clean up own statistic ${item.id}`);
    }
    const remaining = await request('/stats/cards');
    assert(report.preservedIds.every(id => remaining.items.some(item => item.id === id)), 'Existing cards preserved');
    report.cleanedUpOwnCards = own.map(item => item.id);
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    await context.tracing.stop({ path: path.join(out, 'trace.zip') });
    await context.close();
    await browser.close();
  }
  console.log(`Dashboard full-app verification passed: ${out}`);
})().catch(error => { console.error(error); process.exitCode = 1; });
