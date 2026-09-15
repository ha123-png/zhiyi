// Isolated headless acceptance. Never opens or controls a desktop window.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const out = process.env.ZHIYI_VISUAL_OUT || '.local/tests/design-visual';
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ ...(process.env.ZHIYI_BROWSER_CHANNEL ? {channel:process.env.ZHIYI_BROWSER_CHANNEL} : {}), headless: true });
  const sourceKind = process.env.ZHIYI_OFFLINE ? 'simulated-source' : 'real-ai';
  const report = { browser: browser.version(), sourceKind, url: process.env.ZHIYI_ACCEPTANCE_URL || 'http://127.0.0.1:8813', scenarios: [] };
  let page;
  try {
    page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
    await page.context().tracing.start({screenshots:true,snapshots:true});
    const onboarding = await (await page.request.get(report.url + '/api/v1/user-state/onboarding')).json();
    await page.goto(report.url);
    if (!onboarding.completed_version) {
      await page.getByRole('button', { name: '跳过引导' }).waitFor({ state:'visible' });
      await page.getByRole('button', { name: '跳过引导' }).click();
    }
    await page.getByRole('button', { name: '数据仓库', exact: true }).click();
    await page.getByRole('button', { name: /卡片阅读压力验收.*6/ }).click();
    await page.locator('.content-card').first().waitFor();
    for (const [width, height] of (process.env.ZHIYI_FAST ? [[1366,768]] : [[1920,1080],[1366,768],[1280,720],[1101,720],[1099,720],[961,650],[959,650],[801,650],[799,650],[600,480],[400,400],[1280,540],[1366,768]])) {
      await page.setViewportSize({ width, height });
      await page.locator('.app-page-wrap').evaluate(e=>e.scrollTop=0);
      await page.waitForTimeout(240);
      for (const mode of ['table', 'card']) {
        await page.getByRole('button', { name: mode === 'table' ? '表格' : '卡片', exact: true }).click();
        await page.waitForTimeout(240);
        const metrics = await page.evaluate(() => {
          const bounds = selector => document.querySelector(selector)?.getBoundingClientRect().toJSON();
          return { tree: bounds('.sheet-tree'), compact: bounds('.datastore-compact-navigation'), footer: bounds('.table-footer'), cards: [...document.querySelectorAll('.content-card')].map(c => c.getBoundingClientRect().toJSON()), page: bounds('.app-page-wrap'), scrollWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth };
        });
        report.scenarios.push({ width, height, mode, ...metrics });
        assert(width > 800 ? metrics.tree.width > 0 && metrics.compact.height === 0 : metrics.tree.width === 0 && metrics.compact.height > 0, 'Both presentations share navigation breakpoints');
        if (mode === 'card' && width >= 1280 && height >= 720) assert(metrics.footer.bottom <= height + 1, `Pagination below viewport at ${width}x${height}`);
        await page.locator('.table-footer').scrollIntoViewIfNeeded();
        assert(await page.locator('.table-footer').isVisible(), 'Pagination remains reachable');
        if (mode === 'card') {
          const cropped = await page.locator('.content-card-body').evaluateAll(bodies => bodies.flatMap(body => [...body.querySelectorAll('.content-card-field p')].filter(p => p.getBoundingClientRect().height > 0 && p.getBoundingClientRect().bottom > body.getBoundingClientRect().bottom + 1).map(p => p.textContent)));
          assert.equal(cropped.length, 0, `Card paragraphs cropped at ${width}x${height}: ${cropped.map(x=>x.slice(0,30))}`);
        }
      }
      if ([1920,1280,799,400].includes(width)) await page.screenshot({ path: path.join(out, `cards-${width}-${height}.png`) });
    }
    await page.setViewportSize({ width:1366,height:768 });
    const pageOneTitle=await page.locator('.content-card-title').first().textContent();
    await page.getByTitle('下一页',{exact:true}).click();
    await page.waitForFunction(title=>document.querySelector('.pagination .active')?.textContent === '2' && document.querySelector('.content-card-title')?.textContent !== title,pageOneTitle);
    const pageTwoTitle=await page.locator('.content-card-title').first().textContent();
    await page.getByRole('button',{name:'表格',exact:true}).click();
    await page.getByRole('button',{name:'卡片',exact:true}).click();
    await page.waitForFunction(title=>document.querySelector('.pagination .active')?.textContent === '2' && document.querySelector('.content-card-title')?.textContent===title,pageTwoTitle);
    report.scenarios.push({scenario:'presentation-page-memory',passed:true});
    await page.getByRole('button',{name:'查看全文',exact:true}).first().click();
    await page.getByRole('button',{name:'编辑',exact:true}).click();
    await page.locator('.content-card-dialog textarea').first().fill('未保存的窗口缩放验收');
    await page.setViewportSize({width:600,height:650});
    await page.waitForTimeout(250);
    assert.equal(await page.locator('.content-card-dialog textarea').first().inputValue(),'未保存的窗口缩放验收','Resize must not discard an open editing draft');
    await page.getByRole('button',{name:'取消修改',exact:true}).click();
    await page.getByRole('button',{name:'关闭内容详情',exact:true}).click();
    await page.setViewportSize({width:1366,height:768});
    report.scenarios.push({scenario:'open-edit-draft-survives-capacity-change',passed:true});
    await page.getByRole('textbox', { name:'搜索当前表' }).fill('守恒条件');
    await page.locator('.content-card mark').first().waitFor();
    assert(await page.locator('.content-card mark').first().evaluate(mark => { const a=mark.getBoundingClientRect(), b=mark.closest('.content-card-body').getBoundingClientRect(); return a.top >= b.top && a.bottom <= b.bottom; }), 'Deep search hit visible');
    await page.screenshot({ path:path.join(out,'search.png') });
    await page.getByRole('button',{name:'模板',exact:true}).click();
    await page.getByRole('button',{name:/原件与阅读验收/}).first().click();
    await page.getByRole('button',{name:'高级设置',exact:true}).click();
    assert.equal(await page.getByText('给 AI 的理解要求', {exact:false}).count(),0);
    assert.equal(await page.getByRole('button',{name:'创建可编辑副本',exact:true}).count(),0);
    await page.locator('.template-advanced').scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,'template-advanced.png')});
    await page.locator('.app-page-wrap').evaluate(e=>e.scrollTop=0);
    await page.screenshot({path:path.join(out,'template.png')});
    const originalOrder = await page.getByRole('textbox',{name:'字段名',exact:true}).evaluateAll(inputs=>inputs.map(input=>input.value));
    const moving = page.getByRole('button',{name:`移动字段 ${originalOrder[2]}`,exact:true});
    await moving.focus();
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('ArrowUp');
    assert.equal(await page.getByRole('textbox',{name:'字段名',exact:true}).first().inputValue(),originalOrder[2]);
    assert(await moving.evaluate(e=>e===document.activeElement),'Sorting retains keyboard focus');
    const savePattern='**/api/v1/templates/*';
    await page.route(savePattern,async route=>{
      if(route.request().method()==='PUT') await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'验收模拟：版本已更新，请保留编辑后重试。'})});
      else await route.continue();
    });
    await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.getByText('验收模拟：版本已更新，请保留编辑后重试。').waitFor();
    assert.equal(await page.getByRole('textbox',{name:'字段名',exact:true}).first().inputValue(),originalOrder[2]);
    await page.unroute(savePattern);
    await page.getByRole('button',{name:'保存',exact:true}).click();
    await page.getByText(/模板已保存/).waitFor();
    const reordered = await page.getByRole('textbox',{name:'字段名',exact:true}).evaluateAll(inputs=>inputs.map(input=>input.value));
    assert.equal(reordered[0],originalOrder[2]);
    // Drag the same field back down; dropping changes order, never its section.
    await moving.dragTo(page.locator('.template-field').nth(2));
    const orderSaved = page.waitForResponse(response => response.request().method() === 'PUT' && /\/templates\/[^/]+$/.test(response.url()));
    await page.getByRole('button',{name:'保存',exact:true}).click();
    assert.equal((await orderSaved).status(), 200);
    await page.waitForFunction(() => !document.querySelector('.template-list-item[aria-disabled="true"]'));
    await page.getByText(/模板已保存/).waitFor();
    assert.deepEqual(await page.getByRole('textbox',{name:'字段名',exact:true}).evaluateAll(inputs=>inputs.map(input=>input.value)),originalOrder);
    report.scenarios.push({scenario:'keyboard-and-pointer-field-order',passed:true});
    await page.locator('.tpl-item-name').getByText('发票',{exact:true}).click();
    await page.getByRole('heading',{name:'发票',exact:true}).waitFor();
    const naming=page.getByRole('checkbox',{name:'文件命名',exact:true});
    if(!(await naming.isVisible())) await page.getByRole('button',{name:'高级设置',exact:true}).click();
    assert(await naming.isDisabled(),'Builtin naming remains read-only');
    await page.getByRole('button',{name:'复制后编辑',exact:true}).click();
    await naming.waitFor({state:'attached'});
    if(!(await naming.isVisible())) await page.getByRole('button',{name:'高级设置',exact:true}).click();
    await naming.check();
    const copySaved=page.waitForResponse(response=>response.request().method()==='PUT' && /\/templates\/[^/]+$/.test(response.url()));
    await page.getByRole('button',{name:'保存',exact:true}).click();
    const copy=await (await copySaved).json();
    assert.equal(copy.behavior.suggest_filename,true);
    await page.locator('.tpl-item-name').getByText('发票',{exact:true}).click();
    await page.locator('.tpl-item-name').getByText(copy.name,{exact:true}).click();
    await page.getByRole('heading',{name:copy.name,exact:true}).waitFor();
    assert(await naming.isChecked(),'Copied template naming setting survives reopening');
    await page.getByRole('button',{name:'数据仓库',exact:true}).click();
    assert.equal((await page.request.delete(`${report.url}/api/v1/templates/${copy.id}`)).status(),204);
    report.scenarios.push({scenario:'builtin-readonly-copy-naming-save-reopen',passed:true});
    await page.getByRole('button',{name:/原件与阅读验收.*12/}).click();
    await page.getByRole('button',{name:'分组',exact:true}).click();
    await page.getByRole('combobox',{name:/分组方式/}).selectOption('file');
    await page.getByRole('button',{name:'应用分组',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.sheet-tree-view').length===6);
    await page.locator('.sheet-tree-view').first().click();
    await page.waitForFunction(()=>document.querySelector('.row-total-hint')?.textContent.includes('2'));
    await page.getByRole('button',{name:'卡片',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('.content-card').length===2);
    report.scenarios.push({scenario:'six-source-file-groups-two-details-each',passed:true});
    for (const extension of ['pdf','png','txt','docx','xlsx']) {
      await page.getByRole('button',{name:'文件历史',exact:true}).click();
      await page.locator('.hist-tab').filter({hasText:'已完成'}).click();
      await page.getByTitle('查看原文件与提取数据').filter({hasText:`数学练习.${extension}`}).first().click();
      const preview=page.locator('.history-preview');
      await preview.waitFor();
      if (extension==='pdf') {
        await page.waitForFunction(()=>{const canvas=document.querySelector('.history-preview canvas');return document.querySelector('.history-preview .document-preview-error') || (canvas && canvas.width>0 && !document.querySelector('.history-preview .document-preview-loading'));});
        assert.equal(await preview.locator('.document-preview-error').count(),0,`PDF preview failed: ${await preview.locator('.document-preview-error').allTextContents()}`);
      }
      else if(extension==='png') await page.waitForFunction(()=>[...document.querySelectorAll('.history-preview img')].some(img=>img.complete&&img.naturalWidth>0));
      else await preview.locator('.document-text-preview, .document-docx-preview, .document-xlsx-preview').first().waitFor();
      assert.equal(await preview.locator('.document-preview-error').count(),0);
      await page.getByRole('button',{name:'来源与处理详情',exact:true}).click();
      const internal=page.getByRole('button',{name:/知意内部原件/});
      await internal.click();
      assert.equal(await internal.getAttribute('aria-expanded'),'true');
      await page.getByRole('button',{name:'复制文件位置'}).first().scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(out,`source-${extension}.png`)});
      report.scenarios.push({scenario:`original-${extension}`,passed:true});
    }
    await page.evaluate(()=>localStorage.setItem('theme','dark'));
    await page.reload();
    await page.getByRole('button',{name:'设置',exact:true}).click();
    await page.getByRole('button',{name:'限制发送给模型的内容',exact:true}).scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(out,'settings-dark.png')});
    await page.getByRole('button',{name:'限制发送给模型的内容',exact:true}).click();
    if(await page.getByRole('button',{name:'限制发送给模型的内容',exact:true}).getAttribute('aria-pressed')==='false') await page.getByRole('button',{name:'限制发送给模型的内容',exact:true}).click();
    await page.getByRole('spinbutton',{name:'PDF 页数／多帧图片帧数',exact:true}).waitFor();
    await page.screenshot({path:path.join(out,'settings-limits-dark.png')});
    await page.getByRole('button',{name:'数据仓库',exact:true}).click();
    await page.getByRole('button',{name:process.env.ZHIYI_OFFLINE ? /原件与阅读验收.*12/ : /通用会议纪要.*2/}).click();
    if(process.env.ZHIYI_OFFLINE) await page.locator('.sheet-tree-view').filter({hasText:'数学练习.txt'}).first().click();
    await page.getByRole('button',{name:'卡片',exact:true}).click();
    await page.locator('.content-card').first().waitFor();
    assert.equal(await page.locator('.content-card').count(),2);
    await page.screenshot({path:path.join(out,`${sourceKind}-cards-dark.png`)});
    await page.locator('.content-card input[type="checkbox"]').first().check();
    await page.setViewportSize({width:1280,height:720});
    await page.waitForTimeout(220);
    assert(await page.locator('.content-card input[type="checkbox"]').first().isChecked(),'Resize must preserve selection');
    await page.getByRole('button',{name:'表格',exact:true}).click();
    await page.getByRole('button',{name:'卡片',exact:true}).click();
    assert(await page.locator('.content-card input[type="checkbox"]').first().isChecked(),'Presentation switches preserve selection');
    await page.getByRole('button',{name:'查看全文',exact:true}).first().click();
    await page.getByRole('button',{name:'查看原件',exact:true}).click();
    const divider=page.getByRole('separator',{name:'调整内容与原件宽度'});
    await divider.focus();
    await page.keyboard.press('Home');
    assert.equal(await divider.getAttribute('aria-valuenow'),'30');
    // Wait for the dialog's entrance animation and the keyboard resize to settle.
    await divider.hover();
    const dividerBounds=await divider.boundingBox();
    await page.mouse.move(dividerBounds.x+dividerBounds.width/2,dividerBounds.y+40);
    await page.mouse.down();
    await page.mouse.move(dividerBounds.x+140,dividerBounds.y+40,{steps:8});
    await page.mouse.up();
    const ratio=await divider.getAttribute('aria-valuenow');
    assert(Number(ratio)>30,'Source divider responds to pointer drag');
    report.scenarios.push({scenario:'source-divider-keyboard-pointer-memory',passed:true,ratio});
    assert.equal(await page.locator('.content-card-dialog').getByRole('link',{name:'下载完整原件',exact:true}).count(),0);
    await page.locator('.content-card-original .document-text-preview').waitFor();
    await page.screenshot({path:path.join(out,`${sourceKind}-source-divider.png`)});
    await page.getByRole('button',{name:'关闭内容详情',exact:true}).click();
    await page.getByRole('button',{name:'查看全文',exact:true}).first().click();
    await page.getByRole('button',{name:'查看原件',exact:true}).click();
    assert.equal(await divider.getAttribute('aria-valuenow'),ratio);
    await page.setViewportSize({width:600,height:650});
    assert(await page.locator('.content-card-original').isVisible(),'Narrow window still shows source');
    assert(!(await page.locator('.content-card-detail-content').isVisible()),'Narrow source replaces the parallel content pane');
    await page.getByRole('button',{name:'收起原件',exact:true}).click();
    assert(await page.locator('.content-card-detail-content').isVisible());
    await page.setViewportSize({width:1280,height:720});
    await page.getByRole('button',{name:'关闭内容详情',exact:true}).click();
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.evaluate(()=>{document.documentElement.style.fontSize='22px';document.body.style.fontSize='22px';});
    await page.locator('.table-footer').scrollIntoViewIfNeeded();
    assert(await page.locator('.table-footer').isVisible(),'Pagination reachable with enlarged text');
    assert.equal(await page.locator('.content-card-body').evaluateAll(bodies=>bodies.flatMap(body=>[...body.querySelectorAll('.content-card-field p')].filter(p=>p.getBoundingClientRect().height>0 && p.getBoundingClientRect().bottom>body.getBoundingClientRect().bottom+1)).length),0,'Enlarged text is not clipped by card bodies');
    await page.screenshot({path:path.join(out,'enlarged-text-reduced-motion.png')});
    assert.equal(await page.locator('.app-page-wrap').evaluate(e=>getComputedStyle(e).animationName),'none');
    report.scenarios.push({scenario:`${sourceKind}-rendering-selection-text-enlargement-reduced-motion`,passed:true,nativeScaling:false});
    report.passed=true;
  } finally {
    if(page) {
      if(!report.passed) await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});
      await page.context().tracing.stop({path:path.join(out,'journey-trace.zip')}).catch(()=>{});
    }
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(report,null,2));
    await browser.close();
  }
})().catch(error=>{ console.error(error); process.exitCode=1; });
