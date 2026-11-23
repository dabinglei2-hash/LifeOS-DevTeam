/* Isolated browser verification. The caller owns a synthetic monitor server. */
const { chromium } = require(process.env.LADS_PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.LADS_CONTROL_CENTER_TEST_URL;
const artifacts = process.env.LADS_CONTROL_CENTER_TEST_ARTIFACTS;
if (!base || !/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || !artifacts) throw Error('Explicit isolated URL and private artifacts required');
const pages = [['Agent 状态','Agent 运行状态'],['任务流转','任务与时间线'],['成本','Token 与成本'],['QA','QA 与审查'],['GitHub','GitHub 双仓库'],['知识库','工程知识库'],['系统设置','系统设置']];
(async () => {
  fs.mkdirSync(artifacts, {recursive:true});
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const checks=[], errors=[];
  try {
    const request = await browser.newContext();
    for (const [route, options, expected] of [
      ['/api/health',{},200], ['/api/sections/agents',{},200], ['/api/sections/tasks?project=invalid',{},422],
      ['/api/sections/tasks?task_id=invalid',{},422], ['/api/sections/cost?limit=1000',{},422],
      ['/api/sections/cost?since=2026-01-01',{},422], ['/api/sections/settings',{method:'POST'},405],
      ['/api/sections/settings',{headers:{Origin:'https://evil.example'}},403],
      ['/api/sections/settings',{headers:{Host:'evil.example'}},400],
      ['/api/sections/settings',{headers:{'Sec-Fetch-Site':'cross-site'}},403],
      ['/.env',{},404],['/control-center.sqlite3',{},404],['/openapi.json',{},404]
    ]) {
      const response = await request.request.fetch(base+route,options);
      if (response.status()!==expected) throw Error(`${route}: ${response.status()} != ${expected}`);
      checks.push({id:route,status:'PASS'});
    }
    for (const section of ['agents','tasks','cost','qa','github','knowledge','settings']) {
      const response = await request.request.get(base+'/api/sections/'+section+'?project=devteam&limit=10');
      const data = await response.json();
      if (response.status()!==200 || data.project!=='devteam' || data.errors?.length) throw Error(`section ${section}`);
      if (['tasks','qa','cost','github','knowledge'].includes(section) && data.items?.some(row=>row.project!=='devteam')) throw Error('project isolation '+section);
      if (section==='cost' && data.cost_status!=='UNKNOWN') throw Error('fake billing');
      if (section==='knowledge' && !data.items[0].search_verified) throw Error('knowledge search');
      if (section==='github' && data.items[0].status!=='known') throw Error('real remote snapshot missing');
      checks.push({id:'section-'+section,status:'PASS'});
    }
    await request.close();
    for (const viewport of [{width:1440,height:1000},{width:390,height:844},{width:1366,height:600}]) {
      const context=await browser.newContext({viewport,reducedMotion:'reduce'}), page=await context.newPage();
      page.on('pageerror',error=>errors.push(error.message));
      page.on('console',message=>{if(message.type()==='error') errors.push(message.text())});
      await page.goto(base);
      const nav=page.getByRole('navigation',{name:viewport.width<700?'移动端主导航':'主导航',exact:true});
      for (const [label,heading] of pages) {
        await nav.getByRole('button',{name:label,exact:true}).click();
        await page.getByRole('heading',{name:heading,exact:true}).waitFor();
        await page.waitForTimeout(700);
        if (await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)) throw Error('overflow '+viewport.width+' '+label);
        if (await page.getByRole('alert').count()) throw Error('page error '+label);
      }
      await page.screenshot({path:path.join(artifacts,`monitor-${viewport.width}.png`),fullPage:true});
      await page.reload(); await page.getByRole('heading',{name:'系统设置',exact:true}).waitFor();
      await nav.getByRole('button',{name:'任务流转',exact:true}).click();
      await page.getByRole('heading',{name:'任务与时间线',exact:true}).waitFor();
      const detail = page.getByRole('button',{name:'查看时间线',exact:true}).first();
      if(await detail.count()) {await detail.click();await page.getByRole('heading',{name:'任务时间线',exact:true}).waitFor();await page.getByRole('button',{name:'返回任务列表'}).click();}
      checks.push({id:`ui-${viewport.width}x${viewport.height}-7-pages-navigation-refresh-details`,status:'PASS'});
      await context.close();
    }
    if(errors.length) throw Error(errors.join(' | '));
    const result={status:'PASS',executed_by:'main-controller',independent_qa:false,synthetic_monitor:true,checks,console_errors:errors};
    fs.writeFileSync(path.join(artifacts,'browser-evidence.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify({status:'PASS',checks:checks.length}));
  } finally {await browser.close();}
})().catch(error=>{fs.writeFileSync(path.join(artifacts,'browser-failure.json'),JSON.stringify({status:'FAIL',error:String(error)}));console.error(String(error));process.exitCode=1;});
