// Incremental regression for real dashboard controls; use only an isolated fixture.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const url = process.env.ZHIYI_DASHBOARD_TEST_URL;
  if (!url || !/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(url)) throw new Error('Set ZHIYI_DASHBOARD_TEST_URL to an isolated local fixture.');
  const out = process.env.ZHIYI_VISUAL_OUT || '.local/tests/dashboard-interactions';
  fs.mkdirSync(out, {recursive:true});
  const browser = await chromium.launch({headless:true});
  const report = {url, scenarios:[], realModelCalls:0};
  try {
    const page = await browser.newPage({viewport:{width:1280,height:720}, reducedMotion:'reduce'});
    await page.context().tracing.start({screenshots:true,snapshots:true});
    await page.goto(url);
    const onboarding = await (await page.request.get(`${url}/api/v1/user-state/onboarding`)).json();
    if (!onboarding.completed_version) await page.getByRole('button',{name:'跳过引导',exact:true}).click();
    await page.getByRole('button',{name:'仪表盘',exact:true}).click();
    const add = page.getByRole('button',{name:'添加统计',exact:true});
    await add.waitFor();
    await page.locator('.dashboard-metrics[aria-busy="false"]').waitFor();
    const factText = await page.getByRole('button',{name:/^数据记录 /}).textContent();
    const initial = await page.getByRole('article').count();
    assert(initial <= 2, 'Fixture needs room for at least two test statistics.');
    const names = [];
    for (let i=initial; i<4; i++) {
      const name = `自动回归统计 ${Date.now()}-${i}`;
      await add.click();
      await page.getByRole('textbox',{name:'名称',exact:true}).fill(name);
      await page.getByRole('button',{name:'保存统计',exact:true}).click();
      await page.getByRole('article',{name,exact:true}).waitFor();
      names.push(name);
    }
    assert.equal(await add.isDisabled(), true, 'Four-card limit must disable creation.');
    report.scenarios.push({name:'four-card-limit',passed:true});
    // Last-row confirmations must participate in layout and be hit-testable.
    for (const [width,height] of [[1280,720],[900,800],[600,650]]) {
      await page.setViewportSize({width,height});
      const name = names.at(-1);
      for (let round=0; round<2; round++) {
        await page.getByRole('button',{name:`管理统计：${name}`,exact:true}).click();
        await page.getByRole('menuitem',{name:'删除统计',exact:true}).click();
        const confirm = page.getByRole('button',{name:'删除统计',exact:true});
        await confirm.scrollIntoViewIfNeeded();
        await confirm.click({trial:true});
        const box = await page.locator('.statistic-delete-prompt').boundingBox();
        const next = await page.locator('.dashboard-model-panel').boundingBox();
        assert(box && next && box.y+box.height <= next.y+1, 'Delete confirmation overlaps the next panel.');
        if (round===0) await page.screenshot({path:path.join(out,`confirm-${width}.png`)});
        await page.getByRole('button',{name:'取消',exact:true}).click();
        assert.equal(await page.getByRole('article').count(),4);
      }
      report.scenarios.push({name:'delete-confirm-visible-and-repeat-cancel',width,height,rounds:2,passed:true});
    }
    for (const name of names) {
      await page.getByRole('button',{name:`管理统计：${name}`,exact:true}).click();
      await page.getByRole('menuitem',{name:'删除统计',exact:true}).click();
      await page.getByRole('button',{name:'删除统计',exact:true}).click();
      await page.getByRole('article',{name,exact:true}).waitFor({state:'detached'});
    }
    assert.equal(await page.getByRole('article').count(),initial);
    assert.equal(await add.isEnabled(),true);
    assert.equal(await page.getByRole('button',{name:/^数据记录 /}).textContent(),factText);
    report.scenarios.push({name:'delete-restores-capacity-keeps-facts',deleted:names.length,passed:true});
    await page.context().tracing.stop({path:path.join(out,'dashboard-trace.zip')});
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({...report,passed:true},null,2));
    console.log(`Dashboard regression passed: ${report.scenarios.length} scenarios; ${out}`);
  } catch (error) {
    fs.writeFileSync(path.join(out,'failed.json'),JSON.stringify({...report,error:String(error)},null,2));
    throw error;
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
