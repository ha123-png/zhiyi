// Real DashboardPage, Recharts and Fold in an isolated browser fixture.
// All HTTP data is explicitly synthetic; no API process, database or model is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const { createServer } = await import('vite');
  const { default: react } = await import('@vitejs/plugin-react');
  const root = path.resolve('.');
  const out = path.resolve('.local/v040-dashboard-interaction', new Date().toISOString().replaceAll(':', '-'));
  const fixture = path.join(out, 'fixture');
  fs.mkdirSync(fixture, { recursive: true });
  const source = '/@fs/' + path.join(root, 'apps/web/src').replaceAll('\\', '/');
  fs.writeFileSync(path.join(fixture, 'index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>知意仪表盘 · 合成组件验收</title></head><body><div id="root"></div><script type="module" src="/fixture.tsx"></script></body></html>');
  fs.writeFileSync(path.join(fixture, 'fixture.tsx'), `
import React from 'react';
import {createRoot} from 'react-dom/client';
import '${source}/styles.css';
import '${source}/assistant/assistant.css';
import {DashboardPage} from '${source}/components/DashboardPage.tsx';
import {DashboardChart} from '${source}/dashboard/DashboardChart.tsx';
createRoot(document.getElementById('root')!).render(<main style={{maxWidth:1200, margin:'0 auto', padding:'24px 32px'}}>
  <p style={{color:'var(--muted-foreground)',fontSize:12}}>合成组件验收 · 真实组件与动画 · 数据为独立 fixture</p>
  <DashboardPage />
  <section className="dashboard-page dashboard-panel" aria-label="合成条形图" style={{marginTop:24}}>
    <h2>采购分布 · 合成样例</h2>
    <DashboardChart data={[{label:'甲供应方',value:12000},{label:'乙供应方',value:5600},{label:'丙供应方',value:3200}]} type="bar" label="采购金额" unit="CNY" />
  </section>
</main>);
`);
  const report = { synthetic: true, realModelCalls: 0, apiMutations: 0, errors: [], requests: [], cases: [] };
  // Strict independent port: refuse a collision, never reuse acceptance/model services.
  const server = await createServer({ configFile: false, root: fixture, plugins: [react()], server: { host: '127.0.0.1', port: 8919, strictPort: true, fs: { allow: [root] } } });
  let browser;
  try {
    await server.listen();
    const url = server.resolvedUrls.local[0];
    report.url = url;
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1060 }, reducedMotion: 'no-preference' });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    page.on('pageerror', error => report.errors.push(error.message));
    const monthly = Array.from({ length: 33 }, (_, index) => {
      const date = new Date(Date.UTC(2024, index, 1)).toISOString().slice(0, 7);
      return { date, label: date, total: (index * 7) % 13 + 2, bucket: 'month', interval: 1 };
    });
    const daily = Array.from({ length: 7 }, (_, index) => ({ date: `2026-09-${String(index + 7).padStart(2, '0')}`, total: index % 4 + 1, bucket: 'day', interval: 1 }));
    await page.route('**/api/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      report.requests.push(url.pathname + url.search);
      assert.equal(request.method(), 'GET', 'Fixture never mutates a backend');
      const all = url.searchParams.get('days') === 'all';
      const trend = all ? monthly : daily;
      let body;
      if (url.pathname.endsWith('/events')) return route.fulfill({status: 200, contentType: 'text/event-stream', body: ': synthetic fixture\n\n'});
      if (url.pathname.endsWith('/tasks/summary')) body = { total: 252, completed: 252, needs_review: 0, failed: 0 };
      else if (url.pathname.endsWith('/tables')) body = [{id:'synthetic-a',name:'采购明细 · 合成样例',row_count:54},{id:'synthetic-b',name:'费用明细 · 合成样例',row_count:32},{id:'synthetic-c',name:'往来记录 · 合成样例',row_count:18}];
      else if (url.pathname.endsWith('/stats/summary')) body = { row_count:104, table_count:3, template_count:2, new_rows: all ? 104 : 12 };
      else if (url.pathname.endsWith('/stats/trend')) body = trend;
      else if (url.pathname.endsWith('/stats/cards')) body = { items:[], limit:4 };
      else if (url.pathname.endsWith('/stats/overview')) body = { rows_trend:trend.map((point,index) => ({...point, count: all ? index === 0 ? 8 : 3 : index === 0 ? 6 : 1})), trend_bucket: all ? 'month' : 'day', trend_interval:1, templates:[], model_usage:{ calls:all ? 24 : 6, failed:0, elapsed_sample_count:all ? 24 : 6, average_elapsed_ms:1250,total_tokens:all ? 48000 : 12000,calls_with_usage:all ? 24 : 6,calls_with_cache_usage:0,cache_hit_ratio:null,history_note:'合成 fixture；未调用任何模型。',by_purpose:[{purpose:'extraction',model:'合成模型',calls:all ? 24 : 6,failed:0,average_elapsed_ms:1250,total_tokens:all ? 48000 : 12000}]} };
      else throw new Error('Unexpected fixture request: ' + url.pathname);
      return route.fulfill({ status:200, contentType:'application/json', body:JSON.stringify(body) });
    });
    await page.goto(url);
    await page.getByRole('application', {name:'接收文件趋势图'}).waitFor();
    await page.getByRole('combobox', {name:'时间范围'}).selectOption('all');
    await page.getByText('全部保留历史 · 按月', {exact:true}).waitFor();
    for (const endpoint of ['summary', 'trend', 'overview']) assert(report.requests.includes(`/api/v1/stats/${endpoint}?days=all`));
    assert((await page.getByRole('button', {name:/文件任务/}).innerText()).includes('252'));
    await page.waitForTimeout(550);
    await page.screenshot({path:path.join(out,'all-history-1440.png'),fullPage:true});
    report.cases.push({name:'all selection uses all three real API clients, keeps stock unchanged', displayedBucket:'按月',points:monthly.length});

    const focusState = element => {
      const style = getComputedStyle(element);
      return { active:document.activeElement === element, visible:element.matches(':focus-visible'), outlineStyle:style.outlineStyle, outlineWidth:style.outlineWidth, outlineColor:style.outlineColor };
    };
    for (const name of ['接收文件趋势图','数据记录占比图','采购金额条形图']) {
      const chart = page.getByRole('application', {name});
      await chart.scrollIntoViewIfNeeded();
      await chart.click({position:{x:50,y:30}});
      const mouse = await chart.evaluate(focusState);
      assert.equal(mouse.outlineStyle, 'none', `${name}: pointer has no heavy outline`);
      await page.screenshot({path:path.join(out, `mouse-${name}.png`)});
      // Focus through an actual Tab key; programmatic focus alone does not prove modality.
      await page.getByRole('combobox', {name:'时间范围'}).focus();
      for (let attempt = 0; attempt < 50; attempt++) {
        await page.keyboard.press('Tab');
        if ((await chart.evaluate(focusState)).active) break;
      }
      const keyboard = await chart.evaluate(focusState);
      assert(keyboard.active && keyboard.visible, `${name}: keyboard still reaches chart`);
      assert.equal(keyboard.outlineStyle, 'solid');
      assert.equal(keyboard.outlineWidth, '1px');
      await page.screenshot({path:path.join(out, `focus-${name}.png`)});
      report.cases.push({name:`${name} mouse and keyboard focus`,mouse,keyboard});
    }

    const toggle = page.locator('.dashboard-main-trend').getByRole('button',{name:'查看数据',exact:true});
    await toggle.scrollIntoViewIfNeeded();
    const sampleFold = async opening => {
      await page.evaluate(({opening}) => {
        const button = document.querySelector('.dashboard-main-trend .ask-fold-toggle');
        const start = performance.now();
        window.foldSamples = [];
        window.foldDone = false;
        button.click();
        function sample() {
          const body = button.parentElement.querySelector('.ask-fold-body');
          const elapsed = performance.now() - start;
          window.foldSamples.push({elapsed,height:body?.getBoundingClientRect().height ?? 0,opacity:body ? Number(getComputedStyle(body).opacity) : 0});
          if (elapsed < 300) requestAnimationFrame(sample);
          else window.foldDone = true;
        }
        requestAnimationFrame(sample);
      }, {opening});
      await page.waitForFunction(() => window.foldSamples.some(point => point.height > 1 && point.opacity > 0 && point.opacity < .99));
      // CDP captures immediately, without Playwright waiting for layout stability.
      const screenshot = await cdp.send('Page.captureScreenshot', {format:'png'});
      fs.writeFileSync(path.join(out,opening ? 'fold-opening-midframe.png' : 'fold-closing-midframe.png'),Buffer.from(screenshot.data,'base64'));
      await page.waitForFunction(() => window.foldDone);
      const samples = await page.evaluate(() => window.foldSamples);
      assert(samples.some(point => point.height > 1 && point.opacity > 0 && point.opacity < .99), 'Real Fold includes intermediate height and opacity');
      return samples;
    };
    const opening = await sampleFold(true);
    assert.equal(await toggle.getAttribute('aria-expanded'),'true');
    const openedHeight = opening.at(-1).height;
    assert(openedHeight > 50);
    const closing = await sampleFold(false);
    assert.equal(await toggle.getAttribute('aria-expanded'),'false');
    assert.equal(closing.at(-1).height,0);
    report.cases.push({name:'real Fold open and close intermediate frames',opening,closing});

    await page.emulateMedia({reducedMotion:'reduce'});
    // Motion's installed hook reads the OS preference at mount time.
    await page.reload();
    await page.getByRole('combobox', {name:'时间范围'}).selectOption('all');
    await page.getByText('全部保留历史 · 按月', {exact:true}).waitFor();
    await toggle.click();
    await page.waitForTimeout(50);
    const reducedHeight = await page.locator('.dashboard-main-trend .ask-fold-body').evaluate(element => element.getBoundingClientRect().height);
    assert(Math.abs(reducedHeight - openedHeight) < 1, 'Reduced motion reaches open state without a prolonged height animation');
    await toggle.click();
    await page.setViewportSize({width:400,height:900});
    await page.evaluate(() => {document.documentElement.classList.add('dark'); window.scrollTo(0,0);});
    await page.waitForTimeout(220);
    await page.screenshot({path:path.join(out,'all-history-400-dark-reduced.png'),fullPage:true});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),'Narrow fixture has no horizontal overflow');
    report.cases.push({name:'reduced-motion instant Fold and 400px dark layout',reducedHeight});
    assert.deepEqual(report.errors,[]);
    report.passed = true;
  } finally {
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
    await browser?.close();
    await server.close();
  }
  console.log(`Dashboard interaction fixture passed: ${out}`);
})().catch(error => {console.error(error);process.exitCode=1;});
