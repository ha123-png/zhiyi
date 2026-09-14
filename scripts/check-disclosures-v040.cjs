// Synthetic, isolated component browser verification. No model or business API calls.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const project = path.resolve(__dirname, '..');
  const out = path.join(project, '.local/v040-disclosures');
  fs.mkdirSync(out, { recursive: true });
  const source = project.replaceAll('\\', '/');
  const fixture = path.join(out, 'fixture.tsx');
  fs.writeFileSync(fixture, `import React from 'react';
import {createRoot} from 'react-dom/client';
import '${source}/apps/web/src/styles.css';
import '${source}/apps/web/src/components/cards.css';
import {FileNameConfirmation} from '${source}/apps/web/src/components/FileNameConfirmation';
import {TaskFailureDetails} from '${source}/apps/web/src/components/TaskFailureDetails';
import {ExtractPage} from '${source}/apps/web/src/components/ExtractPage';
import {TemplatesPage} from '${source}/apps/web/src/components/TemplatesPage';
const mode=new URLSearchParams(location.search).get('mode');
const task=(window as any).__fixture.task;
createRoot(document.getElementById('root')!).render(mode==='extract'?<ExtractPage initialTask={task}/>:mode==='templates'?<TemplatesPage/>:<main style={{maxWidth:720,margin:'40px auto',padding:'0 20px'}}>
<h1>折叠交互 · 合成验证</h1>
<FileNameConfirmation original="原始记录.png" state={{status:'confirmed',suggested_filename:'青禾纸业采购记录.png',confirmed_filename:null,source_fields:[],explanation:'来自合成记录'}} onChange={()=>{}}/>
<TaskFailureDetails taskId="synthetic"/>
<details className="support data-source-details"><summary>本页有局部读取的数据 · 查看来源范围</summary><p>此处使用合成来源说明，验证原生详情展开及收起时经过中间高度。</p><p>原件第 1–3 页。</p></details>
<details className="template-restoration-log"><summary>最近恢复记录</summary><ul><li>从第 2 版恢复到第 1 版</li><li>从第 3 版恢复到第 2 版</li></ul></details>
</main>);`);
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
  await page.route(url+'*', async route => {
    const html = await server.transformIndexHtml('/__assistant_fixture', `<html lang="zh-CN"><head><meta charset="UTF-8"><title>问知意合成验收</title><style>.ask-trigger{display:none}</style></head><body><script>window.__fixture=${JSON.stringify({task})}</script><div id="root"></div><script type="module" src="/@fs/${fixture.replaceAll('\\', '/')}"></script></body></html>`);
    await route.fulfill({ contentType: 'text/html', body: html });
  });
  page.on('pageerror', e => report.errors.push(e.message));
  const template={id:'synthetic-template',name:'合成票据',version:1,is_system:false,is_active:true,builtin_key:null,source_template_id:null,description:'验证字段折叠',extra_instructions:'',
    fields:[{key:'total',label:'金额',section:'header',value_type:'number',example:'120',instructions:'记录实际金额'}],
    validation_rules:[],deterministic_rules:[],output_mapping:{},created_at:'2026-09-13T00:00:00Z',updated_at:'2026-09-13T00:00:00Z'};
  const task={id:'synthetic',filename:'原始记录.png',content_type:'image/png',size_bytes:1,page_count:1,sha256:'synthetic',template_mode:'manual',template_id:template.id,template_version:1,candidate_templates:[],status:'completed',duplicate_of_task_id:null,created_at:'2026-09-13T00:00:00Z',updated_at:'2026-09-13T00:00:00Z'};
  const result={header:{total:120},items:[]};
  const extraction={task_id:task.id,document_kind:'custom',template_id:template.id,template_version:1,template,model_name:'fixture',prompt_version:'fixture',elapsed_seconds:1,review_version:1,original_result:result,result,validation_issues:[],evidence:[]};
  await page.route('**/api/v1/**',async route=>{
    const req=route.request(),url=new URL(req.url());
    assert.equal(req.method(),'GET','Only synthetic reads are allowed');
    let response={};
    if(url.pathname.endsWith('/templates')) response=[template];
    else if(url.pathname.endsWith('/tables')||url.pathname.endsWith('/tasks')||url.pathname.endsWith('/models/profiles')) response=[];
    else if(url.pathname.endsWith('/result')) response=extraction;
    else if(url.pathname.endsWith('/diagnostics')) response={detail:'合成诊断：本次请求未调用任何模型。\n'.repeat(6),attempt:1,code:'synthetic'};
    else if(url.pathname.endsWith('/local-export')) response={revision:0,enabled:false,parent_path:null,destination:null};
    await route.fulfill({json:response});
  });
  async function nativeFrames(locator){
    return locator.evaluate(async details=>{
      const samples=[];const start=performance.now();
      details.querySelector('summary').click();
      while(performance.now()-start<260){
        await new Promise(requestAnimationFrame);
        samples.push({at:Math.round(performance.now()-start),height:parseFloat(getComputedStyle(details,'::details-content').blockSize)});
      }
      return samples;
    });
  }
  async function gridFrames(button,bodySelector){
    return button.evaluate(async (button,selector)=>{
      const samples=[];const start=performance.now();button.click();
      while(performance.now()-start<260){
        await new Promise(requestAnimationFrame);
        samples.push({at:Math.round(performance.now()-start),height:document.querySelector(selector).getBoundingClientRect().height});
      }
      return samples;
    },bodySelector);
  }
  function hasIntermediate(frames){
    const heights=frames.map(f=>f.height),min=Math.min(...heights),max=Math.max(...heights);
    return max-min>8 && heights.some(h=>h>min+1&&h<max-1);
  }
  try{
    await page.goto(url);
    assert(await page.evaluate(()=>CSS.supports('interpolate-size: allow-keywords')),'Browser supports native height interpolation');
    for(const selector of ['details.presentation-settings','details.task-failure-details','details.data-source-details','details.template-restoration-log']){
      const locator=page.locator(selector);await locator.scrollIntoViewIfNeeded();
      const opened=await nativeFrames(locator),closed=await nativeFrames(locator);
      assert(hasIntermediate(opened),'Native opening has intermediate height: '+selector);
      assert(hasIntermediate(closed),'Native closing has intermediate height: '+selector);
      report.cases.push({name:selector,opened,closed});
    }
    await page.screenshot({path:path.join(out,'native-disclosures-light.png')});
    await page.goto(url+'?mode=extract');
    const validation=page.getByRole('button',{name:/校验结果/});
    await validation.waitFor();await validation.scrollIntoViewIfNeeded();
    let closed=await gridFrames(validation,'#extract-validation-content');
    assert(hasIntermediate(closed),'Extract validation closes gradually');
    assert.equal(await page.locator('#extract-validation-content').getAttribute('aria-hidden'),'true');
    let opened=await gridFrames(validation,'#extract-validation-content');
    assert(hasIntermediate(opened),'Extract validation opens gradually');
    report.cases.push({name:'actual ExtractPage validation',opened,closed});
    await page.screenshot({path:path.join(out,'extract-validation-light.png')});
    await page.goto(url+'?mode=templates');
    const field=page.getByRole('button',{name:'展开示例',exact:true});
    await field.waitFor();await field.scrollIntoViewIfNeeded();
    opened=await gridFrames(field,'#template-field-detail-0');
    assert(hasIntermediate(opened),'Actual template field opens gradually');
    closed=await gridFrames(page.getByRole('button',{name:'收起示例',exact:true}),'#template-field-detail-0');
    assert(hasIntermediate(closed),'Actual template field closes gradually');
    report.cases.push({name:'actual TemplatesPage field explanation',opened,closed});
    await page.getByRole('button',{name:'展开示例',exact:true}).click();
    await page.waitForTimeout(200);
    await page.screenshot({path:path.join(out,'template-field-light.png')});
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.goto(url);
    await page.evaluate(()=>document.documentElement.classList.add('dark'));
    const native=page.locator('details.presentation-settings');
    const reduced=await nativeFrames(native);
    const duration=await native.evaluate(e=>getComputedStyle(e,'::details-content').transitionDuration);
    assert.equal(duration,'0s');
    assert(!hasIntermediate(reduced));
    report.cases.push({name:'reduced motion native details',duration});
    await page.screenshot({path:path.join(out,'native-disclosures-dark-reduced.png')});
    assert.deepEqual(report.errors,[]);report.passed=true;
  }finally{
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
    await context.tracing.stop({path:path.join(out,'trace.zip')});
    await context.close();await browser.close();await server.close();
  }
  console.log('Disclosure browser verification passed: '+out);
})().catch(error=>{console.error(error);process.exitCode=1});
