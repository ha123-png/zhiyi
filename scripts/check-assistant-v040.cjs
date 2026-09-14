// Synthetic, isolated component browser verification. No model or business API calls.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const project = path.resolve(__dirname, '..');
  const out = path.join(project, '.local/v040-assistant-ui');
  fs.mkdirSync(out, { recursive: true });
  const source = project.replaceAll('\\', '/');
  const fixture = path.join(out, 'fixture.tsx');
  fs.writeFileSync(fixture, `import React from 'react';
import {createRoot} from 'react-dom/client';
import '${source}/apps/web/src/styles.css';
import {AssistantProvider} from '${source}/apps/web/src/assistant/AssistantProvider';
import {AssistantPage,AssistantDrawer,AssistantTrigger} from '${source}/apps/web/src/assistant/AssistantSurface';
createRoot(document.getElementById('root')!).render(<AssistantProvider><main className="app-page-wrap ask-page-wrap" style={{height:'100dvh',display:'flex'}}><AssistantPage /></main><AssistantTrigger /><AssistantDrawer /></AssistantProvider>);`);
  const { createServer } = await import('vite');
  const server = await createServer({ root: path.join(project, 'apps/web'), server: { host: '127.0.0.1', port: 0, strictPort: false } });
  await server.listen();
  const url = `http://127.0.0.1:${server.httpServer.address().port}/__assistant_fixture`;
  const browser = await chromium.launch({ headless: true });
  const report = { url, realModelCalls: 0, cases: [], errors: [] };
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: path.join(out, 'video'), size: { width: 1440, height: 900 } } });
  await context.tracing.start({ screenshots: true, snapshots: true });
  const page = await context.newPage();
  const devtools = await context.newCDPSession(page);
  await page.route(url, async route => {
    const html = await server.transformIndexHtml('/__assistant_fixture', `<html lang="zh-CN"><head><meta charset="UTF-8"><title>问知意合成验收</title><style>.ask-trigger{display:none}</style></head><body><div id="root"></div><script type="module" src="/@fs/${fixture.replaceAll('\\', '/')}"></script></body></html>`);
    await route.fulfill({ contentType: 'text/html', body: html });
  });
  page.on('pageerror', e => report.errors.push(e.message));
  const analysis = {
    analysis_id: 'synthetic-purchase', source: { table_id: 'table', table_name: '合成采购明细', row_count: 6, document_count: 4, grain: 'auto', generated_at: '2026-09-13T10:00:00Z', request: { table_id: 'table', dimensions: ['supplier'], metrics: [{ op: 'sum', field: 'total' }] } },
    data: [{ label: '上海样本供应商一 · 精密设备与耗材服务中心', 'sum:total': 126800 }, { label: '杭州样本供应商二', 'sum:total': 68240 }, { label: '南京样本供应商三', 'sum:total': 18460 }, { label: '未发生采购', 'sum:total': 0 }],
    metric_keys: ['sum:total'], metric_labels: { 'sum:total': '采购金额' }, warnings: [], truncated: false,
  };
  const chart = { analysis_id: analysis.analysis_id, type: 'bar', title: '采购金额按供应商分布', x: 'label', series: ['sum:total'] };
  const tool = { id: 'chart', name: 'render_chart', status: 'completed', result: { chart, analysis } };
  const catalog = { id: 'catalog', name: 'catalog', status: 'completed', result: { kind: 'tables', items: [{ id: 'table', name: '合成采购明细' }] } };
  const thread = { id: 'thread', title: '按供应商查看采购金额，保留来源依据与完整原件以便核对，这是用于验证标题截断的合成长对话名称', archived: false, profile_id: 'local', updated_at: '2026-09-13T10:00:00Z', runs: [], tools: [catalog, tool], messages: [
    { id: 'user', role: 'user', context: { table_id: 'table', table_name: '合成采购明细' }, created_at: '2026-09-13T10:00:00Z', parts: [{ type: 'text', text: '各供应商的采购金额怎样分布？' }] },
    { id: 'answer', role: 'assistant', context: {}, created_at: '2026-09-13T10:00:00Z', parts: [{ type: 'tool', ...catalog }, { type: 'text', text: '已按供应商汇总采购金额。上海样本供应商占比最高，可展开数据与来源核对原始记录。' }, { type: 'tool', ...tool }] },
  ] };
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), u = new URL(req.url());
    assert.equal(req.method(), 'GET', `Unexpected mutation: ${u.pathname}`);
    let result = {};
    if (u.pathname.endsWith('/models/profiles')) result = [{ id: 'local', name: '本地合成验收模型', model_name: 'synthetic-only', provider: 'lm_studio', version: 1, base_url: 'http://127.0.0.1:1234/v1', is_remote: false, is_archived: false }];
    else if (u.pathname.endsWith('/assistant/settings')) result = { profile_id: 'local' };
    else if (u.pathname.endsWith('/assistant/threads')) result = u.searchParams.has('archived') ? [] : [thread];
    else if (u.pathname.endsWith('/assistant/threads/thread')) result = thread;
    else if (u.pathname.endsWith('/assistant/resources')) result = [{ id: 'table', kind: 'table', name: analysis.source.table_name }];
    await route.fulfill({ json: result });
  });
  try {
    await page.goto(url);
    await page.locator('.ask-thread-title').click();
    await page.locator('.ask-analysis').waitFor();
    await page.locator('.ask-analysis').scrollIntoViewIfNeeded();
    await page.waitForTimeout(350);
    const bar = page.locator('.recharts-bar-rectangle path').first();
    await bar.hover();
    await page.locator('.ask-chart-readout .ask-chart-tooltip').waitFor();
    const overlap = await page.evaluate(() => {
      const readout = document.querySelector('.ask-chart-readout').getBoundingClientRect();
      const graphic = document.querySelector('.ask-chart-canvas .recharts-wrapper').getBoundingClientRect();
      return { readoutBottom: readout.bottom, graphicTop: graphic.top, cursor: !!document.querySelector('.recharts-tooltip-cursor') };
    });
    assert(overlap.readoutBottom <= overlap.graphicTop + 1, 'Tooltip must stay above the plot');
    assert.equal(overlap.cursor, false, 'Bar has no intersecting cursor');
    await page.screenshot({ path: path.join(out, '1440-light-bar-hover.png') });
    report.cases.push({ name: 'bar hover does not cover plot', ...overlap });
    await page.getByRole('button', { name: '查找资料 · 已完成', exact: true }).click();
    assert(await page.locator('.ask-process .ask-resource-link').isVisible(), 'One click opens single tool result');
    await page.getByRole('button', { name: '查找资料 · 已完成', exact: true }).click();
    await page.getByRole('combobox', { name: '图表展示方式' }).selectOption('donut');
    await page.locator('.ask-analysis').scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
    const firstLegend = page.locator('.ask-chart-legend button').first();
    await firstLegend.focus();
    assert.match(await page.locator('.ask-donut-center strong').innerText(), /126,800/);
    await page.screenshot({ path: path.join(out, '1440-light-donut-keyboard.png') });
    report.cases.push({ name: 'donut legend keyboard exposes exact category amount' });
    await page.getByRole('button', { name: '数据与来源', exact: true }).click();
    assert(await page.locator('.ask-analysis-evidence table').isVisible());
    await page.screenshot({ path: path.join(out, '1440-light-evidence.png') });
    await page.getByRole('button', { name: '数据与来源', exact: true }).click();
    for (const [width, height, dark, zoom] of [[1440,900,true,1],[1280,800,false,1],[1024,768,false,1],[600,780,false,1],[400,740,true,1],[1280,900,false,1.25]]) {
      const cssWidth = Math.round(width / zoom), cssHeight = Math.round(height / zoom);
      await page.setViewportSize({ width: cssWidth, height: cssHeight });
      await devtools.send('Emulation.setDeviceMetricsOverride', { width: cssWidth, height: cssHeight, deviceScaleFactor: zoom, mobile: false });
      await page.evaluate(({ dark }) => {
        document.documentElement.classList.toggle('dark', dark);
      }, { dark, zoom });
      await page.locator('.ask-analysis').scrollIntoViewIfNeeded();
      await page.waitForTimeout(280);
      const metrics = await page.evaluate(() => ({ viewport: innerWidth, width: document.documentElement.scrollWidth, send: document.querySelector('[aria-label="发送问题"]').getBoundingClientRect().toJSON() }));
      assert(metrics.width <= cssWidth + 1, `Horizontal page overflow ${width}`);
      assert(metrics.send.right <= cssWidth + 1 && metrics.send.bottom <= cssHeight + 1, `Composer unreachable ${width}`);
      await page.screenshot({ path: path.join(out, `${width}-${dark ? 'dark' : 'light'}-${zoom}.png`) });
      await page.getByRole('button', { name: '对话设置', exact: true }).click();
      const settings = await page.locator('.ask-settings-popover').boundingBox();
      assert(settings.x >= -1 && settings.x + settings.width <= cssWidth + 1, `Settings overflow ${width}`);
      await page.getByRole('button', { name: '关闭对话设置' }).click();
      if (width <= 600) {
        await page.getByRole('button', { name: '展开对话列表' }).click();
        await page.getByRole('button', { name: '关闭对话列表' }).click();
        assert(await page.getByRole('button', { name: '展开对话列表' }).isVisible(), 'History can close without selecting a thread');
      }
      report.cases.push({ name: 'responsive theme and zoom', width, height, dark, zoom, metrics });
    }
    await devtools.send('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(() => { document.querySelector('.ask-trigger').style.display = 'grid'; });
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.getByRole('button', { name: '问知意', exact: true }).click();
    await page.getByRole('button', { name: /展开采购金额/ }).click();
    await page.getByRole('button', { name: '关闭展开图表' }).click();
    assert(await page.getByRole('dialog', { name: '快捷问知意' }).isVisible(), 'Closing chart retains drawer');
    await page.getByRole('button', { name: '关闭快捷问知意' }).click();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.getByRole('combobox', { name: '图表展示方式' }).selectOption('horizontal_bar');
    const animation = await page.locator('.ask-rank-fill').first().evaluate(e => getComputedStyle(e).animationName);
    assert.equal(animation, 'none');
    report.cases.push({ name: 'nested dialog and reduced motion' });
    assert.deepEqual(report.errors, []);
    report.passed = true;
  } finally {
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
    await context.tracing.stop({ path: path.join(out, 'trace.zip') });
    await context.close();
    await browser.close();
    await server.close();
  }
  console.log(`Assistant UI verification passed: ${out}`);
})().catch(error => { console.error(error); process.exitCode = 1; });
