// Synthetic, isolated component browser verification. No model or business API calls.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const project = path.resolve(__dirname, '..');
  const out = path.join(project, '.local/v040-review-frontend');
  fs.mkdirSync(out, { recursive: true });
  const source = project.replaceAll('\\', '/');
  const fixture = path.join(out, 'fixture.tsx');
  fs.writeFileSync(fixture, `import React from 'react';
import {createRoot} from 'react-dom/client';
import '${source}/apps/web/src/styles.css';
import {AssistantProvider} from '${source}/apps/web/src/assistant/AssistantProvider';
import {AssistantPage,AssistantDrawer,AssistantTrigger} from '${source}/apps/web/src/assistant/AssistantSurface';
createRoot(document.getElementById('root')!).render(<AssistantProvider><aside id="control-samples" style={{position:'fixed',top:8,right:8,zIndex:100,display:'flex',gap:6}}><button className="btn primary">主要按钮</button><button className="btn primary" disabled>禁用主要按钮</button><button className="btn secondary">次要按钮</button></aside><main className="app-page-wrap ask-page-wrap" style={{height:'100dvh',display:'flex'}}><AssistantPage /></main><AssistantDrawer /></AssistantProvider>);`);
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
    analysis_id:'comparison', source:{ table_id:'table',table_name:'合成采购明细',row_count:6,document_count:4,grain:'auto',generated_at:'2026-09-13T10:00:00Z',request:{} },
    data:[{label:'样本供应商一','sum:total':12680,'count:*':3},{label:'样本供应商二','sum:total':6824,'count:*':2},{label:'样本供应商三','sum:total':1846,'count:*':1}],
    metric_keys:['sum:total','count:*'], metric_labels:{'sum:total':'采购金额','count:*':'记录数'},warnings:[],truncated:false,
  };
  const chart = { id:'chart',name:'render_chart',status:'completed',result:{analysis,chart:{analysis_id:analysis.analysis_id,type:'composed',title:'采购金额与记录数',x:'label',series:analysis.metric_keys}}};
  const catalog = {id:'catalog',name:'catalog',status:'completed',result:{kind:'tables',items:[{id:'table',name:'合成采购明细'}]}};
  const item = (row,before,after) => ({label:'调整数值',name:'合成采购明细',operation:{kind:'increment_rows',table_id:'table',changes:{amount:1}},columns:{amount:'金额'},affected:[{row_id:row,name:row===1?'采购编号：SYN-20260913-001 · 青禾纸业采购服务中心':'采购编号：SYN-20260913-002',before:{amount:before},after:{amount:after}}],before:null,after:null});
  const proposal = {id:'plan',name:'propose_operations',status:'pending',result:{kind:'operation_plan',title:'调整数值 · 合成采购明细',items:[item(1,12680,12681),item(2,6824,6825),item(1,12680,12681)]}};
  const threadFor=(id,title,tools)=>({id,title,archived:false,profile_id:'local',updated_at:'2026-09-13T10:00:00Z',runs:[],tools,messages:[
    {id:id+'-u',role:'user',context:{table_id:'table',table_name:'合成采购明细'},created_at:'2026-09-13T10:00:00Z',parts:[{type:'text',text:title}]},
    {id:id+'-a',role:'assistant',context:{},created_at:'2026-09-13T10:00:00Z',parts:[...tools.map(tool=>({type:'tool',...tool})),{type:'text',text:id==='chart-thread'?'采购金额和记录数分别使用左右刻度。':'请核对上方变更，确认后执行。'}]},
  ]});
  const threads=[threadFor('chart-thread','采购金额与记录数对照',[catalog,chart]),threadFor('plan-thread','把两条记录的金额各加 1',[proposal])];
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),u=new URL(req.url());
    assert.equal(req.method(),'GET','Unexpected mutation '+u.pathname);
    let result={};
    if(u.pathname.endsWith('/models/profiles')) result=[{id:'local',name:'合成验收模型',model_name:'synthetic-only',provider:'lm_studio',version:1,base_url:'http://127.0.0.1:1234/v1',is_remote:false,is_archived:false}];
    else if(u.pathname.endsWith('/assistant/settings')) result={profile_id:'local'};
    else if(u.pathname.endsWith('/assistant/threads')) result=u.searchParams.has('archived')?[]:threads;
    else if(/assistant\/threads\/[^/]+$/.test(u.pathname)) result=threads.find(t=>u.pathname.endsWith('/'+t.id));
    else if(u.pathname.endsWith('/assistant/resources')) result=[];
    await route.fulfill({json:result});
  });
  try {
    await page.goto(url);
    await page.locator('.ask-thread-title').filter({hasText:'采购金额与记录数对照'}).click();
    await page.locator('[data-series-kind="line"]').waitFor();
    assert.equal(await page.locator('[data-series-kind="bar"] rect').count(),1);
    assert.equal(await page.locator('[data-series-kind="line"] line').getAttribute('stroke-dasharray'),'5 4');
    assert.equal(await page.locator('[data-series-kind="line"] circle').count(),1);
    report.cases.push({name:'composed legend matches bar and dashed line with marker'});
    const fold=page.getByRole('button',{name:'查找资料 · 已完成',exact:true});
    await fold.scrollIntoViewIfNeeded();
    const frames=await fold.evaluate(async button=>{
      const parent=button.parentElement;
      button.click();
      const samples=[];const start=performance.now();
      while(performance.now()-start<270){
        await new Promise(requestAnimationFrame);
        samples.push({at:Math.round(performance.now()-start),height:parent.querySelector('.ask-fold-body')?.getBoundingClientRect().height||0});
      }
      return samples;
    });
    const finalHeight=frames.at(-1).height;
    assert(finalHeight>20&&frames.some(f=>f.height>0&&f.height<finalHeight-1),'Fold needs a real intermediate frame');
    report.cases.push({name:'short in-place fold animation',frames});
    await fold.click();
    await page.locator('.ask-analysis').scrollIntoViewIfNeeded();
    await page.locator('#control-samples').evaluate(e=>e.style.visibility='hidden');
    await page.screenshot({path:path.join(out,'1440-light-composed-legend.png')});
    await page.locator('.ask-thread-title').filter({hasText:'把两条记录的金额各加 1'}).click();
    const card=page.locator('.ask-operation-card');
    await card.waitFor();
    assert.equal(await card.locator('table').count(),1);
    assert.equal(await card.locator('tbody tr').count(),2);
    assert(await card.getByText('影响 2 条记录 · 2 处字段变更').isVisible());
    const appearance=await card.evaluate(e=>{const s=getComputedStyle(e);return {left:s.borderLeftWidth,right:s.borderRightWidth,radius:s.borderTopLeftRadius,bg:s.backgroundColor}});
    assert.equal(appearance.left,'1px');assert.equal(appearance.right,'1px');assert(parseFloat(appearance.radius)>=10);
    const actions=page.locator('.ask-message-assistant .ask-message-actions');
    await actions.scrollIntoViewIfNeeded();await page.mouse.move(1,1);
    const before=await actions.evaluate(e=>({opacity:getComputedStyle(e).opacity,buttons:[...e.querySelectorAll('button')].map(b=>getComputedStyle(b).backgroundColor)}));
    await actions.locator('button').first().hover();
    await page.waitForTimeout(180);
    const after=await actions.evaluate(e=>({opacity:getComputedStyle(e).opacity,buttons:[...e.querySelectorAll('button')].map(b=>getComputedStyle(b).backgroundColor)}));
    assert.equal(before.opacity,'1');assert.equal(after.opacity,'1');
    assert.notEqual(before.buttons[0],after.buttons[0]);
    assert.equal(before.buttons[1],after.buttons[1]);
    report.cases.push({name:'merged approval and independent message action hover',appearance,before,after});
    for(const dark of [false,true]){
      await page.setViewportSize({width:1440,height:900});
      await page.evaluate(dark=>document.documentElement.classList.toggle('dark',dark),dark);
      await page.waitForTimeout(180);
      await page.locator('#control-samples').evaluate(e=>e.style.visibility='visible');
      const primary=page.getByRole('button',{name:'主要按钮',exact:true});
      const disabled=page.getByRole('button',{name:'禁用主要按钮',exact:true});
      const color=async locator=>locator.evaluate(e=>({bg:getComputedStyle(e).backgroundColor,color:getComputedStyle(e).color,opacity:getComputedStyle(e).opacity,outline:getComputedStyle(e).outlineWidth}));
      await page.mouse.move(1,1);const idle=await color(primary),disabledBefore=await color(disabled);
      await primary.hover();await page.waitForTimeout(180);const hover=await color(primary);
      assert.notEqual(hover.bg,hover.color);assert.notEqual(hover.bg,idle.bg);
      await disabled.hover({force:true});await page.waitForTimeout(180);assert.deepEqual(await color(disabled),disabledBefore);
      await page.keyboard.press('Tab');await primary.focus();assert.equal((await color(primary)).outline,'2px');
      report.cases.push({name:'primary, focus and disabled states',dark,idle,hover,disabled:disabledBefore});
      await page.locator('#control-samples').evaluate(e=>e.style.visibility='hidden');
      for(const width of [1440,1024,400]){
        await page.setViewportSize({width,height:900});await card.scrollIntoViewIfNeeded();await page.waitForTimeout(200);
        assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No page horizontal overflow');
        assert(await page.getByRole('button',{name:'确认执行',exact:true}).isVisible());
        assert.equal(await card.locator('tbody tr').first().locator('td').nth(1).evaluate(e=>getComputedStyle(e).whiteSpace),'nowrap','Long record names must not wrap the amount digits');

        await page.screenshot({path:path.join(out,width+'-'+(dark?'dark':'light')+'-approval.png')});
        report.cases.push({name:'approval narrow and theme',width,dark});
      }
    }
    await page.setViewportSize({width:1440,height:900});
    await page.locator('.ask-thread-title').filter({hasText:'采购金额与记录数对照'}).click();
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.reload();
    await page.locator('.ask-thread-title').filter({hasText:'采购金额与记录数对照'}).click();
    await page.getByRole('button',{name:'查找资料 · 已完成',exact:true}).click();
    assert(await page.locator('.ask-process .ask-resource-link').isVisible());
    const arrowTransition=await page.locator('.ask-process .ask-fold-toggle svg').evaluate(e=>getComputedStyle(e).transitionDuration);
    assert.equal(arrowTransition,'0s');
    report.cases.push({name:'reduced motion disclosure',arrowTransition});
    assert.deepEqual(report.errors,[]);
    report.passed=true;
  } finally {
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
    await context.tracing.stop({path:path.join(out,'trace.zip')});
    await context.close();await browser.close();await server.close();
  }
  console.log('Frontend review verification passed: '+out);
})().catch(error=>{console.error(error);process.exitCode=1});
