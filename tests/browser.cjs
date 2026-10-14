/* Real server/browser smoke test. No camera, model or internet needed. */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
const data = fs.mkdtempSync(path.join(os.tmpdir(), 'railwatcher-browser-'));
const port = Number(process.env.TEST_PORT || 18787);
const base = `http://127.0.0.1:${port}`;
const server = process.env.RAILWATCHER_EXTERNAL_SERVER ? {exitCode:null,stderr:{on(){}},stdout:{on(){}},kill(){}} : spawn(process.env.RAILWATCHER_PYTHON || '.venv/bin/python', ['-m', 'railwatcher', '--port', String(port), '--data', data, '--model', path.join(data, 'missing.onnx')], {stdio: ['ignore', 'pipe', 'pipe']});
let logs = '';
server.stderr.on('data', d => logs += d.toString());
server.stdout.on('data', d => logs += d.toString());
async function main() {
  for (let i=0;i<100;i++) {
    try { if ((await fetch(base)).ok) break; } catch {}
    if (server.exitCode !== null) throw new Error(logs);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const browser = await chromium.launch({headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const errors = []; page.on('pageerror', e=>errors.push(e.message));
    // Online tile services are external to the app; test graceful offline behavior.
    await page.route(/https:\/\/(server\.arcgisonline|tiles\.openrailwaymap)/, route=>route.abort());
    await page.goto(base);
    await page.getByRole('heading',{name:'Video source'}).waitFor();
    await page.getByRole('link',{name:'Railwatcher by Trains.FYI home'}).waitFor();
    await page.getByRole('button',{name:'Switch to dark mode'}).click();
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
    await page.reload();
    await page.getByRole('heading',{name:'Video source'}).waitFor();
    assert.equal(await page.locator('html').getAttribute('data-theme'),'dark','Theme preference persists on reload');
    await page.getByRole('button',{name:'Switch to light mode'}).click();
    fs.mkdirSync('artifacts',{recursive:true});
    await page.screenshot({path:'artifacts/setup-desktop.png',fullPage:true});
    await page.getByLabel('Camera name').fill('West junction');
    await page.getByRole('button',{name:/Network stream/}).click();
    assert.equal(await page.getByLabel('RTSP stream URL').getAttribute('type'),'text');
    await page.getByLabel('RTSP stream URL').fill('rtsp://user:private-camera-password@127.0.0.1:18554/live');
    await page.getByRole('button',{name:/Continue/}).click();
    await page.getByLabel('Latitude',{exact:true}).fill('43.6426');
    await page.getByLabel('Longitude',{exact:true}).fill('-79.3871');
    await page.getByRole('button',{name:'Show coordinates'}).click();
    await page.locator('#bearing').fill('135');
    await page.screenshot({path:'artifacts/location-desktop.png',fullPage:true});
    await page.getByRole('button',{name:/Continue/}).click();
    await page.getByRole('button',{name:'Connect to TrainsFYI'}).waitFor();
    await page.getByRole('button',{name:/Continue/}).click();
    await page.getByText(/Train model is not installed/).waitFor();
    await page.getByRole('button',{name:/Start watching/}).click();
    await page.getByRole('heading',{name:'West junction'}).waitFor();
    await page.getByText('No sightings recorded').waitFor();
    await page.getByRole('button',{name:'Pause detection'}).click();
    await page.getByRole('button',{name:'Start detection'}).waitFor();
    await page.screenshot({path:'artifacts/dashboard-desktop.png',fullPage:true});
    await page.getByRole('button',{name:'View live camera fullscreen'}).click();
    await page.waitForFunction(()=>Boolean(document.fullscreenElement));
    assert.equal(await page.evaluate(()=>Boolean(document.fullscreenElement)),true,'Live view enters fullscreen');
    await page.getByRole('button',{name:'Exit live camera fullscreen'}).click();
    await page.waitForFunction(()=>!document.fullscreenElement);
    await page.getByRole('button',{name:'Review footage'}).click();
    await page.getByRole('heading',{name:'Review footage'}).waitFor();
    await page.getByText(/No archived footage is available yet/).waitFor();
    await page.getByRole('button',{name:'Back to dashboard'}).click();
    await page.getByRole('heading',{name:'West junction'}).waitFor();
    const candidates = [
      {id:'motion.mp4',started:1791640000,ended:1791640120,video_url:'/test-motion.mp4',motion:{state:'motion',windows:[{started:12,ended:20},{started:60,ended:80}]}},
      {id:'quiet.mp4',started:1791639880,ended:1791640000,video_url:'/test-quiet.mp4',motion:{state:'quiet',windows:[]}},
      {id:'pending.mp4',started:1791639760,ended:1791639880,video_url:'/test-pending.mp4',motion:{state:'pending',windows:[]}},
    ];
    await page.route('**/api/buffer?*',route=>{
      const filter=new URL(route.request().url()).searchParams.get('movement');
      return route.fulfill({json:{available_seconds:360,active:true,motion_counts:{motion:1,quiet:1,pending:1,error:0},segments:candidates.filter(row=>filter==='all'||row.motion.state===filter)}});
    });
    await page.getByRole('button',{name:'Review footage'}).click();
    await page.getByText('1 segments with train detections', {exact:false}).waitFor();
    await page.getByLabel('Look for').selectOption('movement');
    await page.getByText('1 segments with significant movement', {exact:false}).waitFor();
    assert.equal(await page.locator('.review-segment').count(),1);
    assert.equal(await page.locator('.motion-jump').count(),2);
    assert.ok(await page.getByLabel('Start time',{exact:true}).inputValue());
    await page.getByLabel('Look for').selectOption('red_flashes');
    await page.getByText('1 segments with red flashes',{exact:false}).waitFor();
    assert.equal(await page.getByLabel('Show footage').inputValue(),'motion');
    await page.getByLabel('Look for').selectOption('movement');
    await page.getByText('1 segments with significant movement',{exact:false}).waitFor();
    await page.getByLabel('Show footage').selectOption('all');
    await page.waitForFunction(()=>document.querySelectorAll('.review-segment').length===3);
    await page.getByLabel('Show footage').selectOption('pending');
    await page.getByRole('button',{name:/Not analysed ·/}).waitFor();
    assert.equal(await page.locator('.review-segment').count(),1);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile footage review overflows');
    await page.screenshot({path:'artifacts/movement-mobile.png',fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    await page.getByLabel('Show footage').selectOption('error');
    await page.getByText(/No footage matches this filter yet/).waitFor();
    assert.equal(await page.locator('#mark-form').isVisible(),false);
    await page.goto(base+'/?footage=motion.mp4');
    await page.getByRole('heading',{name:'Review footage'}).waitFor();
    await page.waitForFunction(()=>document.querySelectorAll('.review-segment').length===3);
    assert.equal(await page.getByLabel('Show footage').inputValue(),'all');
    assert.equal(await page.locator('.review-segment.selected').getAttribute('data-id'),'motion.mp4');
    await page.getByRole('button',{name:'Back to dashboard'}).click();
    await page.unroute('**/api/buffer?*');
    const saved = await page.evaluate(async()=> (await fetch('/api/config')).json());
    assert.equal(saved.bearing,135); assert.equal(saved.latitude,43.6426);
    assert.equal(saved.enabled,false); assert.equal(saved.has_api_key,false);
    assert.equal(saved.source,null); assert.ok(!JSON.stringify(saved).includes('private-camera-password'));
    await page.goto(base);
    await page.getByRole('heading',{name:'West junction'}).waitFor();
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'artifacts/dashboard-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile page overflows');
    await page.getByRole('button',{name:/Camera settings/}).click();
    await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
    await page.getByLabel('RTSP stream URL').waitFor();
    assert.equal(await page.getByLabel('RTSP stream URL').getAttribute('type'),'text');
    await page.getByRole('button',{name:'Match railway points',exact:true}).click();
    await page.getByRole('heading',{name:'Match railway points',exact:true}).waitFor();
    const image=page.locator('#mask-canvas');
    // A synthetic visible preview makes the image coordinate contract testable.
    await page.evaluate(()=>{const img=document.querySelector('#preview-image');img.hidden=false;img.style.height='180px';img.style.width='100%';});
    for(const [imageX,mapX] of [[.25,.3],[.75,.7]]) {
      const box=await image.boundingBox();
      await page.mouse.click(box.x+box.width*imageX,box.y+box.height*.4);
      const mapBox=await page.locator('#map').boundingBox();
      await page.mouse.click(mapBox.x+mapBox.width*mapX,mapBox.y+mapBox.height*.5);
    }
    await page.getByText('2 matched pairs',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Save matched points'}).click();
    await page.getByRole('heading',{name:'Settings',exact:true}).waitFor();
    const calibration=await page.evaluate(async()=> (await fetch('/api/config')).json());
    assert.equal(calibration.railway_calibration.points.length,2);
    assert.ok(calibration.railway_calibration.revision!=='new');
    await page.screenshot({path:'artifacts/settings-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'Mobile settings overflow');
    await page.getByRole('button',{name:'Add landmark',exact:true}).click();
    await page.getByLabel('Landmark label',{exact:true}).fill('Crossing light <left>');
    await page.locator('#mask-canvas').scrollIntoViewIfNeeded();
    const canvasBox=await page.locator('#mask-canvas').boundingBox();
    await page.mouse.move(canvasBox.x+canvasBox.width*.2,canvasBox.y+canvasBox.height*.2);
    await page.mouse.down();
    await page.mouse.move(canvasBox.x+canvasBox.width*.35,canvasBox.y+canvasBox.height*.4,{steps:5});
    await page.mouse.up();
    assert.equal(await page.getByLabel('Image editing mode').inputValue(),'landmarks');
    await page.screenshot({path:'artifacts/landmarks-mobile.png',fullPage:true});
    await page.getByLabel('Camera direction').fill('180');
    await page.getByRole('button',{name:'Save settings'}).click();
    await page.getByText('Settings saved.').waitFor();
    const updated = await page.evaluate(async()=> (await fetch('/api/config')).json());
    assert.equal(updated.bearing,180); assert.equal(updated.name,'West junction');
    assert.equal(updated.landmarks.length,1); assert.equal(updated.landmarks[0].label,'Crossing light <left>');
    for(const [i,value] of [.2,.2,.35,.4].entries()) assert.ok(Math.abs(updated.landmarks[0].box[i]-value)<.01);
    assert.equal(await page.getByLabel('Landmark label',{exact:true}).inputValue(),'Crossing light <left>');
    await page.getByLabel('Image editing mode').selectOption('landmarks');
    await page.locator('#mask-canvas').scrollIntoViewIfNeeded();
    const redrawBox=await page.locator('#mask-canvas').boundingBox();
    await page.mouse.move(redrawBox.x+redrawBox.width*.6,redrawBox.y+redrawBox.height*.6);
    await page.mouse.down();
    await page.mouse.move(redrawBox.x+redrawBox.width*.4,redrawBox.y+redrawBox.height*.3,{steps:5});
    await page.mouse.up();
    await page.getByRole('button',{name:'Save settings'}).click();
    await page.getByText('Settings saved.').waitFor();
    const redrawn=await page.request.get(`${base}/api/config`).then(r=>r.json());
    assert.equal(redrawn.landmarks[0].id,updated.landmarks[0].id);
    for(const [i,value] of [.4,.3,.6,.6].entries()) assert.ok(Math.abs(redrawn.landmarks[0].box[i]-value)<.01);
    await page.getByRole('button',{name:'Remove selected landmark'}).click();
    await page.getByRole('button',{name:'Save settings'}).click();
    await page.getByText('Settings saved.').waitFor();
    assert.deepEqual((await page.request.get(`${base}/api/config`).then(r=>r.json())).landmarks,[]);

    await page.getByRole('button',{name:'Back to dashboard'}).click();
    await page.getByRole('heading',{name:'West junction'}).waitFor();
    assert.equal((await page.request.get(`${base}/api/config`)).status(),200);
    assert.equal((await page.request.get(`${base}/api/sightings`)).status(),200);
    assert.equal(fs.existsSync(path.join(data,'admin.token')),false);
    assert.deepEqual(errors,[]);
    console.log('Browser smoke passed: open access, setup, offline map, secret redaction, dashboard, pause, persistence, mobile.');
  } finally {await browser.close();}
}
main().catch(error=>{console.error(error);console.error(logs);process.exitCode=1;}).finally(()=>server.kill('SIGTERM'));
