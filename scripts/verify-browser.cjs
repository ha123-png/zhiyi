// Runs from a clean synthetic directory; does not reuse or stop any existing app.
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { once } = require('node:events');
const { randomUUID } = require('node:crypto');
const project = path.resolve(__dirname, '..');
const python = process.env.ZHIYI_TEST_PYTHON || path.join(project, 'apps/api/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const root = path.join(project, '.local/browser-verification', `${new Date().toISOString().replaceAll(':','-')}-${randomUUID().slice(0,8)}`);
fs.mkdirSync(root, { recursive: true });
fs.writeFileSync(path.join(project,'.local/browser-verification/latest.json'),JSON.stringify({root}));
const env = { ...process.env, PYTHONUTF8:'1', ZHIYI_OFFLINE:'1', ZHIYI_VISUAL_OUT:path.join(root,'visual') };
delete env.ZHIYI_FAST;
function run(executable, args, name) {
  return new Promise((resolve,reject) => {
    const log=fs.createWriteStream(path.join(root,`${name}.log`));
    const child=spawn(executable,args,{cwd:project,env,windowsHide:true});
    child.stdout.pipe(log); child.stderr.pipe(log);
    child.on('error',reject);
    child.on('close',code=>{log.end(() => {
      if(code===0) return resolve();
      // These logs come only from this run's synthetic fixtures and UI checks.
      console.error(fs.readFileSync(path.join(root,`${name}.log`),'utf8').slice(-12000));
      reject(new Error(`${name} failed (${code}); see ${root}`));
    });});
  });
}
(async()=>{
  if(!fs.existsSync(path.join(project,'apps/web/dist/index.html'))) throw new Error('Build the frontend before running browser verification.');
  await run(python,['scripts/prepare-source-acceptance.py','--data-dir',path.join(root,'data'),'--files-dir',path.join(root,'files')],'fixtures');
  const socket=net.createServer(); socket.listen(0,'127.0.0.1'); await once(socket,'listening');
  const port=socket.address().port; await new Promise(resolve=>socket.close(resolve));
  env.ZHIYI_ACCEPTANCE_URL=`http://127.0.0.1:${port}`;
  env.ZHIYI_ACCEPTANCE_API=`${env.ZHIYI_ACCEPTANCE_URL}/api/v1`;
  env.ZHIYI_FIXTURE_DIR=path.join(root,'pressure-files');
  const log=fs.createWriteStream(path.join(root,'server.log'));
  const server=spawn(python,['scripts/serve-verification.py','--data-dir',path.join(root,'data'),'--port',String(port)],{cwd:project,env,windowsHide:true});
  let serverError; server.on('error',e=>{serverError=e;});
  server.stdout.pipe(log); server.stderr.pipe(log);
  const stop=()=>{if(server.exitCode===null) server.kill();};
  process.once('SIGINT',stop); process.once('SIGTERM',stop);
  try {
    let ready=false;
    for(let i=0;i<80;i++) {
      if(serverError) throw serverError;
      if(server.exitCode!==null) throw new Error('Verification server stopped during startup.');
      try { const response=await fetch(env.ZHIYI_ACCEPTANCE_API+'/templates',{signal:AbortSignal.timeout(1000)}); if(response.ok) {ready=true;break;} } catch {}
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    if(!ready) throw new Error('Verification server startup timed out.');
    await run(python,['scripts/prepare-card-acceptance.py'],'pressure');
    await run(process.execPath,['scripts/check-design-layout.cjs'],'existing-journey');
    await run(process.execPath,['scripts/check-trust-layout.cjs'],'trust-journey');
    fs.writeFileSync(path.join(root,'passed.json'),JSON.stringify({passed:true,realModelCalls:0}));
    console.log(`Browser verification passed. Evidence: ${root}`);
  } finally {
    stop(); if(server.exitCode===null) await once(server,'exit'); log.end();
    process.removeListener('SIGINT',stop); process.removeListener('SIGTERM',stop);
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
