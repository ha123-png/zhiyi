// Actual product UI with synthetic data; never targets an installed workspace.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.ZHIYI_ACCEPTANCE_URL || 'http://127.0.0.1:8813';
const root = path.resolve(process.env.ZHIYI_ACCEPTANCE_ROOT || '.local/tmp/2026-10-30-polish/acceptance');
const allowed = path.resolve('.local') + path.sep;
assert(root.startsWith(allowed), 'Acceptance fixtures must stay in this workspace .local directory');
assert(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Acceptance must target a local isolated instance');
const out = path.join(root, `ui-evidence-${new Date().toISOString().replace(/[:.]/g, '-')}`);
fs.mkdirSync(out, {recursive: true});
const report = {passed: false, synthetic: true, modelCalls: 0, checks: []};
(async () => {
  const browser = await chromium.launch({headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, recordVideo: {dir: path.join(out, 'motion'), size: {width: 1440, height: 1000}}});
  const page = await context.newPage();
  const issues = [];
  page.on('pageerror', error => issues.push(error.message));
  async function picture(name) {
    await page.evaluate(async () => Promise.all(document.getAnimations().filter(a => a.effect?.getComputedTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
    await page.screenshot({path: path.join(out, name + '.png')});
  }
  async function get(url) { const response = await page.request.get(base + '/api/v1' + url); assert(response.ok(), `${url}: ${response.status()}`); return response.json(); }
  try {
    const fixtures = JSON.parse(fs.readFileSync(path.join(root, 'data/fixtures.json'), 'utf8'));
    const file = fixtures.files[0];
    const historyBefore = await get(`/tasks/${file.task_id}/revisions`);
    await page.goto(base);
    try { await page.getByRole('button', {name: '跳过引导'}).click({timeout: 2000}); } catch {}
    await page.getByRole('button', {name: '数据仓库', exact: true}).click();
    await page.getByText('采购记录', {exact: true}).first().click();
    await page.getByRole('button', {name: '卡片', exact: true}).click();
    const cards = page.locator('.file-data-card');
    await cards.nth(2).waitFor();
    assert.equal(await cards.count(), 3, 'Wide warehouse pages show one row of three cards');
    const cardBox = await cards.first().boundingBox();
    const cardGrid = await page.locator('.warehouse-card-grid').boundingBox();
    const lastBox = await cards.last().boundingBox();
    assert(lastBox.y + lastBox.height <= cardGrid.y + cardGrid.height, 'Cards stay inside the allocated grid');
    await cards.first().getByRole('checkbox').check();
    const selectedBox = await cards.first().boundingBox();
    assert.equal(selectedBox.y, cardBox.y, 'Selecting a card does not insert a row above it');
    assert.equal(selectedBox.height, cardBox.height);
    await cards.first().getByRole('checkbox').uncheck();
    await picture('01-file-cards');
    report.checks.push('one row of three file cards; selection keeps geometry');

    await cards.first().getByRole('button', {name: '查看与编辑'}).click();
    const detail = page.locator('dialog.file-card-dialog');
    await detail.getByRole('button', {name: '编辑共享字段'}).waitFor();
    const body = detail.locator('.content-card-detail-content');
    const nextSection = detail.getByRole('button', {name: /^明细 ·/});
    const positionBefore = await nextSection.boundingBox();
    const footerBefore = await detail.locator('.file-detail-footer').boundingBox();
    await detail.getByRole('button', {name: '编辑共享字段'}).click();
    assert(Math.abs((await nextSection.boundingBox()).y - positionBefore.y) < 1, 'Short-text editing must not push down details');
    assert.deepEqual(await detail.locator('.file-detail-footer').boundingBox(), footerBefore);
    const revisedCustomer = `南山文创·第${historyBefore.total + 1}轮核对`;
    await detail.getByRole('textbox', {name: '客户', exact: true}).fill(revisedCustomer);
    await detail.getByRole('button', {name: '关闭来源详情'}).click();
    const confirmation = page.getByRole('dialog', {name: '放弃未提交的修改？'});
    await confirmation.waitFor();
    await picture('02-unsaved-confirmation');
    await page.keyboard.press('Escape');
    await confirmation.waitFor({state: 'hidden'});
    assert.equal(await detail.getByRole('textbox', {name: '客户', exact: true}).inputValue(), revisedCustomer);
    await detail.getByRole('button', {name: '修订历史', exact: true}).click();
    const history = page.getByRole('dialog', {name: '文件修订历史'});
    await history.locator('.revision-item').first().waitFor();
    assert.equal(await history.getByRole('button', {name: /升级前/}).count(), 0);
    await picture('03-file-history-cards');
    await history.getByRole('button', {name: '关闭文件修订历史'}).click();
    assert.equal(await detail.getByRole('textbox', {name: '客户', exact: true}).inputValue(), revisedCustomer);
    await picture('04-compact-edit');
    await detail.getByRole('button', {name: '提交修改'}).click();
    await detail.getByRole('textbox', {name: '客户', exact: true}).waitFor({state: 'hidden'});
    assert.equal((await get(`/tasks/${file.task_id}/current-details`)).header.customer, revisedCustomer);
    report.checks.push('fixed edit geometry; discard confirmation; history leaves input intact; same current value after save');

    await detail.getByRole('button', {name: '原件对照'}).click();
    await detail.locator('.document-text-preview').waitFor();
    await body.evaluate(e => {e.scrollTop = 450;});
    await detail.locator('.content-card-original').evaluate(e => {e.scrollTop = 220;});
    await page.waitForTimeout(150);
    const scroll = await body.evaluate(e => e.scrollTop);
    const originalScroll = await detail.locator('.content-card-original').evaluate(e => e.scrollTop);
    await detail.getByRole('button', {name: '收起对照'}).click();
    await detail.getByRole('button', {name: '原件对照'}).click();
    await detail.locator('.document-text-preview').waitFor();
    await page.waitForFunction(value => Math.abs(document.querySelector('.content-card-original').scrollTop - value) < 2, originalScroll);
    assert(Math.abs(await body.evaluate(e => e.scrollTop) - scroll) < 2);
    const split = detail.getByRole('separator', {name: '调整内容与原件宽度'});
    await split.focus(); await page.keyboard.press('End');
    assert.equal(await split.getAttribute('aria-valuenow'), '70');
    await page.keyboard.press('Home');
    assert.equal(await split.getAttribute('aria-valuenow'), '30');
    await picture('05-original-comparison');
    await detail.getByRole('button', {name: '更多数据操作'}).click();
    const item = page.getByRole('menuitem', {name: '清空此来源数据'});
    await item.waitFor();
    const gap = await item.evaluate(e => parseFloat(getComputedStyle(e).gap));
    assert(gap >= 4 && gap <= 12, 'Action icon stays next to its label');
    await picture('06-local-action-menu');
    await page.keyboard.press('Escape');
    report.checks.push('resizable original comparison; two independent scroll positions preserved; compact action menu');
    await detail.getByRole('button', {name: '关闭来源详情'}).click();
    await page.getByRole('button', {name: '文件历史', exact: true}).first().click();
    await page.waitForFunction(async ({base, id}) => {
      const data = await fetch(`${base}/api/v1/tasks/${id}/revisions`).then(r => r.json());
      return data.revisions[0].sealed;
    }, {base, id: file.task_id});
    assert.equal((await get(`/tasks/${file.task_id}/revisions`)).total, historyBefore.total + 1);
    report.checks.push('navigation seals exactly one warehouse revision for this edit');
    assert.deepEqual(issues, []);
    report.passed = true;
  } catch (error) {
    report.error = error.stack;
    await page.screenshot({path: path.join(out, 'failure.png')}).catch(() => {});
    throw error;
  } finally {
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    await context.close(); await browser.close();
  }
})().then(() => console.log(JSON.stringify(report))).catch(error => { console.error(error.message); process.exitCode = 1; });
