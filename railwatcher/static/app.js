const $ = (q) => document.querySelector(q);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config, step = 0, page = 'setup', map, marker, wedge, previewTimer, statusTimer, offset = 0;
let sourceType = 'usb', locationChosen = false, detectionMask = [];
const stepNames = ['Camera', 'Location', 'Upload', 'Review'];
function updateThemeToggle() {
  const dark = document.documentElement.dataset.theme === 'dark';
  const button = $('#theme-toggle');
  if (!button) return;
  button.setAttribute('aria-pressed', String(dark));
  button.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} mode`);
  $('#theme-label').textContent = dark ? 'Light mode' : 'Dark mode';
  $('.theme-icon').textContent = dark ? '☀' : '☾';
  $('meta[name="theme-color"]').content = '#1e40af';
}
$('#theme-toggle').onclick = () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('railwatcher-theme', theme); } catch {}
  updateThemeToggle();
};
updateThemeToggle();
async function api(path, method = 'GET', body) {
  const response = await fetch(`/api/${path}`, {method, headers: {'Content-Type':'application/json','X-Railwatcher':'1'}, body:body === undefined ? undefined : JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) { throw new Error(typeof result.detail === 'string' ? result.detail : 'Something went wrong. Please try again.'); }
  return result;
}
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; setTimeout(() => $('#toast').hidden = true, 4500); }
function error(text) { const box = $('#error'); if (box) { box.textContent = text; box.hidden = false; } else toast(text); }
function cleanup() { clearTimeout(previewTimer); clearTimeout(statusTimer); if (document.fullscreenElement) document.exitFullscreen().catch(()=>{}); if (map) { map.stop(); map.remove(); map = null; marker = null; wedge = null; } }
async function boot() {
  try {
    config = await api('config');
    sourceType = config.source_type;
    detectionMask = config.detection_mask || [];
    if (config.setup_complete) { if (new URLSearchParams(location.search).has("footage")) reviewPage(); else dashboard(); }
    else setup(0);
  } catch(e) {
    $('#app').innerHTML = `<section class="card"><h1>Railwatcher is unavailable</h1><p>${escapeHTML(e.message)}</p><button id="reload">Retry</button></section>`;
    $('#reload').onclick = boot;
  }
}
function previewHTML(variant = '', editable = true) {
  return `<section class="card preview-card ${variant}"><div class="preview-head"><h3>Live camera</h3><span class="preview-status" id="preview-badge">Waiting</span></div><div class="preview"><div class="preview-empty" id="preview-empty"><strong>No camera frame</strong><p id="preview-message">Select a camera to view its picture.</p></div><img id="preview-image" alt="Live camera preview" hidden><canvas id="mask-canvas" aria-label="Camera detection area overlay"></canvas>${editable?'':'<button class="preview-fullscreen" type="button" aria-label="View live camera fullscreen">Full screen</button>'}</div>${editable?`<div class="mask-editor"><div class="mask-editor-head"><strong>Detection area</strong><span id="mask-count"></span></div><p>Click the image to outline where trains appear. The detector ignores objects outside this area.</p><div class="mask-editor-actions"><button class="secondary" type="button" id="mask-undo">Undo point</button><button class="text-button" type="button" id="mask-clear">Clear area</button></div></div>`:`<div class="preview-foot"><p>Live view · processed on this device</p></div>`}</section>`;
}
function bindFullscreen() {
  const button=$('.preview-fullscreen');
  if(!button)return;
  const viewer=button.closest('.preview');
  button.onclick=async()=>{
    try { if(document.fullscreenElement===viewer) await document.exitFullscreen(); else await viewer.requestFullscreen(); }
    catch { toast('Fullscreen is unavailable in this browser.'); }
  };
  document.onfullscreenchange=()=>{ if(button.isConnected){const full=document.fullscreenElement===viewer;button.textContent=full?'Exit full screen':'Full screen';button.setAttribute('aria-label',full?'Exit live camera fullscreen':'View live camera fullscreen');} };
}
function drawMask() {
  const canvas = $('#mask-canvas');
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(rect.width * ratio) || canvas.height !== Math.round(rect.height * ratio)) {
    canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0); context.clearRect(0, 0, rect.width, rect.height);
  const points = detectionMask.map(([x, y]) => [x * rect.width, y * rect.height]);
  if (points.length >= 3) {
    context.fillStyle = 'rgba(9, 18, 32, .48)'; context.fillRect(0, 0, rect.width, rect.height);
    context.globalCompositeOperation = 'destination-out'; context.beginPath();
    points.forEach(([x, y], i) => i ? context.lineTo(x, y) : context.moveTo(x, y));
    context.closePath(); context.fill(); context.globalCompositeOperation = 'source-over';
  }
  if (points.length) {
    context.beginPath(); points.forEach(([x, y], i) => i ? context.lineTo(x, y) : context.moveTo(x, y));
    if (points.length >= 3) context.closePath();
    context.strokeStyle = '#d6eb94'; context.lineWidth = 2; context.stroke();
    for (const [x, y] of points) {
      context.beginPath(); context.arc(x, y, 4, 0, Math.PI * 2);
      context.fillStyle = '#d6eb94'; context.fill(); context.strokeStyle = '#142033'; context.lineWidth = 1.5; context.stroke();
    }
  }
  const count = $('#mask-count');
  if (count) count.textContent = detectionMask.length >= 3 ? `${detectionMask.length} points · active` : detectionMask.length ? `${detectionMask.length} of 3 points` : 'Full frame';
  const undo = $('#mask-undo');
  if (undo) undo.disabled = detectionMask.length === 0;
}
function setupMaskEditor() {
  const canvas = $('#mask-canvas');
  if (!canvas) return;
  canvas.addEventListener('click', event => {
    if (detectionMask.length >= 32) return toast('Detection areas can have up to 32 points.');
    const rect = canvas.getBoundingClientRect();
    detectionMask = [...detectionMask, [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ]];
    drawMask();
  });
  $('#mask-undo').onclick = () => { detectionMask = detectionMask.slice(0, -1); drawMask(); };
  $('#mask-clear').onclick = () => { detectionMask = []; drawMask(); };
  window.onresize = drawMask;
  drawMask();
}
function startPreview() {
  clearTimeout(previewTimer);
  bindFullscreen();
  const image = $('#preview-image');
  if (!image) return;
  const tick = async () => {
    if (!image.isConnected) return;
    try {
      const response = await fetch(`/api/preview?t=${Date.now()}`);
      if (!response.ok) throw new Error();
      const blob = await response.blob();
      if (!image.isConnected) return;
      const url = URL.createObjectURL(blob);
      const previous = image.src; image.src = url; if (previous.startsWith('blob:')) URL.revokeObjectURL(previous);
      image.hidden = false; $('#preview-empty').hidden = true; $('#preview-badge').textContent = 'Receiving';
      drawMask();
    } catch {
      if (!image.isConnected) return;
      image.hidden = true; $('#preview-empty').hidden = false; $('#preview-badge').textContent = 'Waiting for video';
      $('#preview-message').textContent = 'No frame received. Check the camera connection and stream address.';
    }
    if (image.isConnected) previewTimer = setTimeout(tick, 1000);
  }; tick();
}
function setup(index) {
  cleanup(); page = 'setup'; step = index;
  const subtitles = ['Camera', 'Location', 'Upload settings', 'Review'];
  const descriptions = ['Choose a video source and check its picture.', 'Set the camera position and the direction it faces.', 'Add the TrainsFYI upload details, if you have them.', 'Check these settings before enabling detection.'];
  $('#app').innerHTML = `<div class="intro"><div><h1>${subtitles[step]}</h1><p>${descriptions[step]}</p></div></div><div class="steps">${stepNames.map((name,i) => `<div class="step ${i===step?'active':''}"><b>${i<step?'✓':i+1}</b>${name}</div>`).join('')}</div><div id="step-content"></div><div id="error" class="error-box" hidden></div><div class="actions"><button class="secondary" id="back">${step ? '← Back' : config.setup_complete ? '← Dashboard' : 'Back'}</button><span class="hint">${step===3?'The camera feed remains on this device.':'Changes are saved on this device.'}</span><button id="next">${step===3?'Start watching':'Continue'} &nbsp; →</button></div>`;
  $('#back').disabled = step===0 && !config.setup_complete;
  $('#back').onclick = () => step ? setup(step-1) : dashboard();
  $('#next').onclick = async () => {
    const button = $('#next'); button.disabled = true; $('#error').hidden = true;
    try { await saveStep(); if (step===3) dashboard(); else setup(step+1); } catch(e) { error(e.message); } finally { if(button.isConnected) button.disabled = false; }
  };
  [cameraStep, locationStep, accountStep, reviewStep][step]();
}
function cameraStep() {
  $('#step-content').innerHTML = `<div class="setup-grid"><section class="card"><h2>Video source</h2><p>Select a USB camera or enter the address of a network stream.</p><label for="name">Camera name</label><input id="name" maxlength="80" value="${escapeHTML(config.name)}" placeholder="e.g. West junction"><div class="camera-choices"><button class="camera-choice" id="usb"><span class="camera-symbol">USB</span>USB camera<span>Connected to this device</span></button><button class="camera-choice" id="rtsp"><span class="camera-symbol">RTSP</span>Network stream<span>RTSP address on your network</span></button></div><div id="camera-input"></div><button class="secondary" id="test-camera" style="margin-top:20px">Save camera settings</button><div class="note">The live camera feed is processed on this device.</div></section>${previewHTML()}</div>`;
  $('#usb').onclick = () => cameraInput('usb'); $('#rtsp').onclick = () => cameraInput('rtsp'); cameraInput(sourceType);
  $('#test-camera').onclick = async () => { const button = $('#test-camera'); button.disabled = true; try { await saveCamera(); startPreview(); toast('Camera settings saved.'); } catch(e) { error(e.message); } finally {button.disabled = false;} };
  setupMaskEditor();
  if (config.setup_complete || config.source_type==='rtsp') startPreview();
}
async function cameraInput(type) {
  sourceType = type; $('#usb').classList.toggle('selected', type==='usb'); $('#rtsp').classList.toggle('selected', type==='rtsp');
  $('#usb').setAttribute('aria-pressed', type==='usb'); $('#rtsp').setAttribute('aria-pressed', type==='rtsp');
  if(type==='rtsp') {
    $('#camera-input').innerHTML = `<label for="source">RTSP stream URL</label><input id="source" type="text" autocomplete="off" spellcheck="false" placeholder="rtsp://username:password@192.168.1.20:554/stream"><p class="hint">${config.source_type==='rtsp' ? `Saved camera: ${escapeHTML(config.source_label)}. Leave blank to keep it.` : 'Find this address in your camera’s settings. Credentials are stored only on the Pi.'}</p>`;
  } else {
    $('#camera-input').innerHTML = `<label for="source">Camera device</label><input id="source" list="devices" value="${escapeHTML(config.source_type==='usb'?config.source:'/dev/video0')}"><datalist id="devices"></datalist><p class="hint" id="device-hint">Checking for connected cameras…</p>`;
    try { const devices = await api('devices'); if(sourceType!=='usb' || !$('#devices')) return; $('#devices').innerHTML = devices.map(d => `<option value="${escapeHTML(d.path)}">${escapeHTML(d.label)}</option>`).join(''); $('#device-hint').textContent = devices.length ? `${devices.length} video device(s) found. Choose your webcam’s capture device.` : 'No USB cameras detected. Plug one in or enter its /dev/video path.'; } catch(e) {error(e.message);}
  }
}
async function saveCamera() {
  const source = $('#source').value.trim();
  if (!source && (sourceType!=='rtsp' || config.source_type!=='rtsp')) throw new Error('Enter a camera address or device.');
  if (detectionMask.length > 0 && detectionMask.length < 3) throw new Error('Add at least three points for a detection area, or clear it to use the full image.');
  config = await api('config','PATCH',{name:$('#name').value.trim(),source_type:sourceType,source:source || null,detection_mask:detectionMask,enabled:false});
}
function locationStep() {
  locationChosen = config.latitude !== null && config.longitude !== null;
  $('#step-content').innerHTML = `<div class="setup-grid"><section class="card"><h2>Camera location</h2><p>Place the marker on the map or enter coordinates. The overlay shows mapped railway lines.</p><div class="inline-inputs"><div><label for="latitude">Latitude</label><input id="latitude" type="number" step="any" min="-85" max="85" placeholder="43.6426" value="${config.latitude??''}"></div><div><label for="longitude">Longitude</label><input id="longitude" type="number" step="any" min="-180" max="180" placeholder="-79.3871" value="${config.longitude??''}"></div></div><div class="map-controls"><button class="secondary" id="go-coordinates">Show coordinates</button><button class="secondary" id="locate">Use my location</button></div><label class="bearing-label" for="bearing"><span>Camera direction</span><span id="bearing-value">${config.bearing}° · ${compass(config.bearing)}</span></label><input id="bearing" type="range" min="0" max="359" value="${config.bearing}"><p class="hint">0° is north, 90° is east. Point the cone where your camera looks.</p><label class="bearing-label" for="fov"><span>Field of view</span><span id="fov-value">${config.field_of_view}°</span></label><input id="fov" type="range" min="10" max="160" value="${config.field_of_view}"><div class="note">Satellite imagery and rail overlays need an internet connection. You can also enter coordinates manually.</div></section><section class="card"><div id="map"></div><p class="map-note" id="map-message">Click the map to place the camera. Drag to adjust.</p></section></div>`;
  mountMap(); bindLocationControls();
}
function mountMap() {
  if(window.L) {
    map = L.map('map', {zoomAnimation:false, fadeAnimation:false, markerZoomAnimation:false}).setView([config.latitude??43.6426,config.longitude??-79.3871],16);
    const satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:'Imagery © Esri, Maxar, Earthstar Geographics and the GIS User Community'}).addTo(map);
    const rails = L.tileLayer('https://tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png',{maxZoom:19,opacity:.85,attribution:'Data <a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>, Style <a href="https://creativecommons.org/licenses/by-sa/2.0/">CC-BY-SA 2.0</a> <a href="https://openrailwaymap.org">OpenRailwayMap</a>'}).addTo(map);
    satellite.on('tileerror',() => $('#map-message').textContent='Satellite imagery unavailable. Check your internet connection or enter coordinates.');
    rails.on('tileerror',() => $('#map-message').textContent='Some railway tiles could not load. You can still place the camera or enter coordinates.');
    L.control.layers({'Satellite':satellite},{'Railways':rails}).addTo(map);
    map.on('click', e=>position(e.latlng.lat,e.latlng.lng));
    if(locationChosen) position(config.latitude, config.longitude);
  } else $('#map-message').textContent = 'Map library unavailable. Enter coordinates to continue.';
}
function bindLocationControls() {
  $('#go-coordinates').onclick = () => {try {const [lat,lon] = coordinates(); position(lat,lon); if(map) map.setView([lat,lon],18);}catch(e){error(e.message);}};
  $('#latitude').oninput = $('#longitude').oninput = () => {locationChosen=false;};
  $('#locate').onclick = () => { if(!navigator.geolocation) return error('Location is unavailable. Enter coordinates or click the map.'); navigator.geolocation.getCurrentPosition(p => {position(p.coords.latitude,p.coords.longitude); if(map) map.setView([p.coords.latitude,p.coords.longitude],18);},()=>error('Location access is unavailable here. Click the map or enter coordinates.'),{timeout:10000}); };
  $('#bearing').oninput = () => {$('#bearing-value').textContent = `${$('#bearing').value}° · ${compass(+$('#bearing').value)}`; drawCone();};
  $('#fov').oninput = () => {$('#fov-value').textContent = `${$('#fov').value}°`; drawCone();};
}
function coordinates() {const a=$('#latitude').value,b=$('#longitude').value; const lat=Number(a),lon=Number(b);if(!a||!b||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180) throw new Error('Choose a location on the map or enter valid latitude and longitude.');return [lat,lon];}
function compass(b) {return ['N','NE','E','SE','S','SW','W','NW'][Math.round(b/45)%8];}
function position(lat,lon) {
  lon = ((lon+180)%360+360)%360-180; if(Math.abs(lat)>85) return;
  locationChosen = true; $('#latitude').value=lat.toFixed(6);$('#longitude').value=lon.toFixed(6);
  if(!map) return;
  if(marker) marker.remove();
  marker=L.marker([lat,lon],{draggable:true,icon:L.divIcon({className:'',html:'<div class="camera-marker"></div>',iconSize:[20,20],iconAnchor:[10,10]})}).addTo(map);
  marker.on('dragend',()=>{const p=marker.getLatLng();position(p.lat,p.lng);}); drawCone();
}
function drawCone() {
  if(!map || !marker) return; if(wedge) wedge.remove(); const p=marker.getLatLng(),b=+$('#bearing').value,f=+$('#fov').value;
  const points=[[p.lat,p.lng]];
  for(let angle=b-f/2;angle<=b+f/2;angle+=f/20){const r=angle*Math.PI/180;points.push([p.lat+Math.cos(r)*.0012,p.lng+Math.sin(r)*.0012/Math.cos(p.lat*Math.PI/180)]);}
  wedge=L.polygon(points,{color:'#d6eb94',weight:2,fillColor:'#d6eb94',fillOpacity:.3,interactive:false}).addTo(map);
}
function accountStep() {
  $('#step-content').innerHTML=`<div class="setup-grid"><section class="card"><h2>TrainsFYI upload</h2><p>Enter the API key and endpoint supplied by TrainsFYI. Leave these fields empty to keep recordings on this device.</p><label for="api-key">TrainsFYI API key</label><input id="api-key" type="password" autocomplete="off" placeholder="${config.has_api_key?'Key saved. Leave blank to keep it.':'API key'}"><label><input id="clear-key" type="checkbox"> Remove saved API key</label><label for="upload-url">Upload endpoint</label><input id="upload-url" type="url" value="${escapeHTML(config.upload_url)}" placeholder="HTTPS upload endpoint"><p class="hint">The key and recordings are sent to this HTTPS endpoint.</p><div class="note"><strong>Upload service not connected</strong><br>The TrainsFYI camera API is not available yet. Recordings remain on this device until an endpoint accepts them.</div></section><section class="setup-aside"><h3>What is uploaded</h3><p>Only recorded train clips and camera location details are sent. Live video stays on this device.</p><p>If the network is unavailable, clips remain queued here for a later retry.</p></section></div>`;
}
function reviewStep() {
  $('#step-content').innerHTML=`<div class="setup-grid"><section class="card"><h2>Review settings</h2>${[['Name',config.name],['Camera',config.source_label],['Position',`${config.latitude?.toFixed(6)}, ${config.longitude?.toFixed(6)}`],['Direction',`${config.bearing}° ${compass(config.bearing)} · ${config.field_of_view}° view`],['TrainsFYI',config.has_api_key&&config.upload_url?'Not verified':'On-device recording']].map(([a,b])=>`<div class="review-item"><span>${a}</span><strong>${escapeHTML(b)}</strong></div>`).join('')}<div class="inline-inputs"><div><label for="confidence">Detection confidence</label><input id="confidence" type="number" min="0.1" max="0.99" step="0.05" value="${config.confidence}"></div><div><label for="storage">Recording storage (GB)</label><input id="storage" type="number" min="1" max="1000" step="0.25" value="${config.storage_gb}"></div></div><p class="hint">${config.pre_roll}s before detection · ${config.post_roll}s after the last detection. Long trains are saved in consecutive clips.</p><div id="model-note" class="note">Checking the local detector…</div></section>${previewHTML()}</div>`;
  setupMaskEditor(); startPreview(); api('status').then(s=>{const note=$('#model-note');if(!note)return;note.textContent=s.model_available?'Train model installed. Detection runs continuously. Use infrared or low-light equipment at night.':'Train model is not installed. Add models/train.onnx to enable detection.';}).catch(e=>error(e.message));
}
async function saveStep() {
  if(step===0) return saveCamera();
  if(step===1) {const [latitude,longitude]=coordinates(); config=await api('config','PATCH',{latitude,longitude,bearing:+$('#bearing').value,field_of_view:+$('#fov').value});}
  if(step===2) config=await api('config','PATCH',{api_key:$('#clear-key').checked?'':($('#api-key').value.trim()||null),upload_url:$('#upload-url').value.trim()});
  if(step===3) config=await api('config','PATCH',{confidence:+$('#confidence').value,storage_gb:+$('#storage').value,setup_complete:true,enabled:true});
}
function dashboard() {
  cleanup(); page='dashboard';offset=0;
  $('#app').innerHTML=`<div class="dashboard-layout"><aside class="dashboard-sidebar">${previewHTML('sidebar-preview',false)}<section class="card event-panel"><div class="card-head"><div><h2>Historical events</h2><p>Train activity caught by this camera</p></div><span class="event-count" id="event-count">—</span></div><div id="sightings" class="event-list"></div><div class="event-pagination"><button id="previous" class="text-button" hidden>← Newer</button><button id="more" class="text-button" hidden>Older →</button></div></section></aside><section class="dashboard-main"><div class="intro"><div><h1>${escapeHTML(config.name)}</h1><p>${config.latitude === null ? 'Location not set' : `${config.latitude.toFixed(5)}, ${config.longitude.toFixed(5)}`} · Facing ${config.bearing}° ${compass(config.bearing)}</p></div><div class="dashboard-actions"><button class="secondary" id="review-footage">Review footage</button><button class="secondary" id="settings">Camera settings</button></div></div><div class="stats"><div class="stat"><div class="stat-label">Sightings</div><div class="stat-value" id="stat-total">—</div><div class="stat-detail">recorded events</div></div><div class="stat"><div class="stat-label">Uploaded</div><div class="stat-value" id="stat-uploaded">—</div><div class="stat-detail">accepted by server</div></div><div class="stat"><div class="stat-label">Queued</div><div class="stat-value" id="stat-pending">—</div><div class="stat-detail">stored on device</div></div><div class="stat"><div class="stat-label">Clip storage</div><div class="stat-value" id="stat-storage">—</div><div class="stat-detail">of ${config.storage_gb} GB limit</div></div></div><section class="card system-card"><div class="card-head"><h2>System status</h2><span class="watch-state" id="watch-badge"><span class="watch-indicator" aria-hidden="true"></span>Starting</span></div><div class="status-list"><div class="status-row"><span>Camera</span><strong id="camera-status">—</strong></div><div class="status-row"><span>Detection</span><strong id="detector-status">—</strong></div><div class="status-row"><span>Footage archive</span><strong id="buffer-status">—</strong></div><div class="status-row"><span>TrainsFYI</span><strong id="upload-status">—</strong></div><div class="status-row"><span>Facing</span><strong>${config.bearing}° ${compass(config.bearing)}</strong></div></div><div id="storage-error" class="error-box" hidden></div><button class="secondary" id="toggle" style="width:100%;margin-top:16px">${config.enabled?'Pause detection':'Start detection'}</button></section></section></div>`;
  $('#settings').onclick=()=>settingsPage();$('#review-footage').onclick=reviewPage;
  $('#toggle').onclick=async()=>{try{config=await api('config','PATCH',{enabled:!config.enabled});$('#toggle').textContent=config.enabled?'Pause detection':'Start detection';toast(config.enabled?'Train detection enabled':'Train detection paused');}catch(e){error(e.message);}};
  $('#more').onclick=()=>{offset+=50;refreshSightings();};$('#previous').onclick=()=>{offset=Math.max(0,offset-50);refreshSightings();};
  startPreview(); refreshDashboard();
}
function localDateTime(epoch) {
  const date=new Date(epoch*1000);const local=new Date(date.getTime()-date.getTimezoneOffset()*60000);
  return local.toISOString().slice(0,19);
}
function reviewPage() {
  cleanup(); page='review';
  $('#app').innerHTML=`<div class="intro"><div><h1>Review footage</h1><p>Find significant movement inside your detection area and review possible train passages.</p></div><button class="secondary" id="back-review">Back to dashboard</button></div><div class="review-layout"><section class="card review-player"><div class="archive-view"><video id="archive-video" controls playsinline preload="metadata"></video><svg class="archive-area" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Tracked detection area"><polygon points="${detectionMask.map(([x,y])=>`${x*100},${y*100}`).join(' ')}"></polygon></svg></div><label for="movement-filter">Show footage</label><select id="movement-filter"><option value="motion">Significant movement in tracked area</option><option value="all">All footage</option><option value="quiet">No significant movement detected</option><option value="pending">Not analysed yet</option><option value="error">Analysis unavailable</option></select><p class="hint">Requires at least 15% of the tracked area to change for about two seconds. Candidates can include vehicles, people, foliage and shadows. They are not confirmed trains. No significant movement detected does not prove an empty track.</p><div class="review-note" id="review-note" role="status">Loading available footage…</div><div id="motion-windows"></div><div class="review-list" id="review-list"></div></section><section class="card mark-card"><h2>Mark a train</h2><p>Select the start and end of the train’s passage. Railwatcher saves it as a manual sighting and queues it for upload when configured.</p><form id="mark-form" hidden><label for="mark-start">Start time</label><input id="mark-start" type="datetime-local" step="1" required><label for="mark-end">End time</label><input id="mark-end" type="datetime-local" step="1" required><button id="mark-submit" type="submit">Mark train and save clip</button></form><p class="hint">Selected footage must be within the available archive and shorter than 15 minutes.</p></section></div>`;
  $('#back-review').onclick=dashboard;
  let selectedId=null, segments=[], requestNumber=0;
  const linkedId=new URLSearchParams(location.search).get('footage');
  let linkedPending=Boolean(linkedId);
  if(linkedPending)$('#movement-filter').value='all';
  const selectWindow=(segment, window)=>{
    const video=$('#archive-video');
    const seek=Math.max(0, window.started-2);
    if(video.readyState>=1)video.currentTime=seek;
    else video.onloadedmetadata=()=>{video.currentTime=seek;video.onloadedmetadata=null;};
    $('#mark-start').value=localDateTime(segment.started+seek);
    $('#mark-end').value=localDateTime(Math.min(segment.ended,segment.started+window.ended+3));
  };
  const selectSegment=id=>{
    const segment=segments.find(row=>row.id===id);if(!segment)return;
    selectedId=id;
    document.querySelectorAll('.review-segment').forEach(button=>button.classList.toggle('selected',button.dataset.id===id));
    const video=$('#archive-video');video.onloadedmetadata=null;video.src=segment.video_url;video.load();
    $('#mark-form').hidden=false;
    $('#mark-start').value=localDateTime(segment.started);$('#mark-end').value=localDateTime(segment.ended);
    const windows=segment.motion.windows;
    $('#motion-windows').innerHTML=windows.length?`<p>Jump to movement:</p>${windows.map((window,index)=>`<button type="button" class="secondary motion-jump" data-index="${index}">${Math.floor(window.started/60)}:${String(Math.floor(window.started%60)).padStart(2,'0')}–${Math.floor(window.ended/60)}:${String(Math.ceil(window.ended%60)).padStart(2,'0')}</button>`).join('')}`:'';
    document.querySelectorAll('.motion-jump').forEach(button=>button.onclick=()=>selectWindow(segment,windows[+button.dataset.index]));
    if(windows.length)selectWindow(segment,windows[0]);
  };
  const refresh=async()=>{
    clearTimeout(statusTimer);
    const request=++requestNumber;
    try {
      const data=await api(`buffer?movement=${$('#movement-filter').value}`);
      if(page!=='review'||request!==requestNumber)return;
      segments=[...data.segments].reverse();
      const counts=data.motion_counts, total=Object.values(counts).reduce((a,b)=>a+b,0);
      const note=$('#review-note');
      note.textContent=total?`${counts.motion} segments with significant movement · ${total-counts.pending} / ${total} analysed · ${counts.pending} waiting · ${counts.error} unavailable · ${(data.available_seconds/3600).toFixed(1)} hours retained`:(data.active?'The camera is building its first footage segment. Check back in a couple of minutes.':'No archived footage is available yet. Start camera monitoring to build the rolling archive.');
      const labels={motion:'Significant movement',quiet:'No significant movement detected',pending:'Not analysed',error:'Analysis unavailable'};
      $('#review-list').innerHTML=segments.length?`<h2>Available times</h2>${segments.map(segment=>`<button type="button" class="review-segment ${segment.id===selectedId?'selected':''}" data-id="${escapeHTML(segment.id)}"><span>${new Date(segment.started*1000).toLocaleString(undefined,{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span><small>${labels[segment.motion.state]} · ${Math.max(1,Math.round((segment.ended-segment.started)/60))} min</small></button>`).join('')}`:total?'<p>No footage matches this filter yet. Analysis continues in the background; try All footage or Not analysed yet.</p>':'';
      document.querySelectorAll('.review-segment').forEach(button=>button.onclick=()=>selectSegment(button.dataset.id));
      if(!segments.some(segment=>segment.id===selectedId)){
        selectedId=null;
        const video=$('#archive-video');video.pause();video.onloadedmetadata=null;video.removeAttribute('src');video.load();
        $('#motion-windows').innerHTML='';$('#mark-form').hidden=true;
        if(segments.length){
          const linked=linkedPending&&segments.find(segment=>segment.id===linkedId);
          selectSegment(linked?linked.id:segments[0].id);
          linkedPending=false;
        }
      }
    }catch(e){if(page==='review'&&request===requestNumber)$('#review-note').textContent=e.message;}
    if(page==='review'&&request===requestNumber)statusTimer=setTimeout(refresh,5000);
  };
  $('#movement-filter').onchange=refresh;
  $('#mark-form').onsubmit=async event=>{
    event.preventDefault();const submit=$('#mark-submit');submit.disabled=true;
    try {
      const result=await api('buffer/mark','POST',{started:Date.parse($('#mark-start').value)/1000,ended:Date.parse($('#mark-end').value)/1000});
      toast('Manual train sighting saved to historical events.');
      if(result.video_url){const video=$('#archive-video');video.onloadedmetadata=null;video.src=result.video_url;video.load();}
    }catch(e){toast(e.message);}
    finally{if(submit.isConnected)submit.disabled=false;}
  };
  refresh();
}
function settingsPage() {
  cleanup(); page='settings'; locationChosen=config.latitude!==null&&config.longitude!==null; sourceType=config.source_type;
  detectionMask = (config.detection_mask || []).map(point => [...point]);
  $('#app').innerHTML=`<div class="intro"><div><h1>Settings</h1><p>Camera, location, detection and upload configuration.</p></div><button class="secondary" id="back-dashboard">Back to dashboard</button></div><form id="settings-form"><div class="settings-grid">
    <section class="card"><h2>Camera</h2><label for="settings-name">Camera name</label><input id="settings-name" maxlength="80" required value="${escapeHTML(config.name)}"><label for="settings-source-type">Video source</label><select id="settings-source-type"><option value="usb" ${sourceType==='usb'?'selected':''}>USB camera</option><option value="rtsp" ${sourceType==='rtsp'?'selected':''}>RTSP network stream</option></select><div id="settings-source-field"></div></section>
    <section class="card"><h2>Location and direction</h2><p>Place the camera on the map. Railway lines are shown over satellite imagery.</p><div class="inline-inputs"><div><label for="latitude">Latitude</label><input id="latitude" type="number" step="any" min="-85" max="85" value="${config.latitude??''}" required></div><div><label for="longitude">Longitude</label><input id="longitude" type="number" step="any" min="-180" max="180" value="${config.longitude??''}" required></div></div><div class="map-controls"><button class="secondary" type="button" id="go-coordinates">Show coordinates</button><button class="secondary" type="button" id="locate">Use my location</button></div><label class="bearing-label" for="bearing"><span>Camera direction</span><span id="bearing-value">${config.bearing}° · ${compass(config.bearing)}</span></label><input id="bearing" type="range" min="0" max="359" value="${config.bearing}"><label class="bearing-label" for="fov"><span>Field of view</span><span id="fov-value">${config.field_of_view}°</span></label><input id="fov" type="range" min="10" max="160" value="${config.field_of_view}"></section>
    <section class="card settings-map"><div id="map"></div><p class="map-note" id="map-message">Click the map to place the camera. Drag to adjust.</p></section>
    <section class="card"><h2>Detection and storage</h2><div class="inline-inputs"><div><label for="confidence">Detection confidence</label><input id="confidence" type="number" min="0.1" max="0.99" step="0.05" value="${config.confidence}"></div><div><label for="confirmations">Confirmations</label><input id="confirmations" type="number" min="1" max="10" step="1" value="${config.confirmations}"></div></div><div class="inline-inputs"><div><label for="pre-roll">Pre-roll (seconds)</label><input id="pre-roll" type="number" min="3" max="30" value="${config.pre_roll}"></div><div><label for="post-roll">Post-roll (seconds)</label><input id="post-roll" type="number" min="3" max="60" value="${config.post_roll}"></div></div><div class="inline-inputs"><div><label for="max-clip">Maximum clip (seconds)</label><input id="max-clip" type="number" min="30" max="900" value="${config.max_clip_seconds}"></div><div><label for="storage">Storage limit (GB)</label><input id="storage" type="number" min="1" max="1000" step="0.25" value="${config.storage_gb}"></div></div></section>
    <section class="card"><h2>TrainsFYI upload</h2><p>Clips stay on this device until an upload endpoint is configured.</p><label for="api-key">API key</label><input id="api-key" type="password" autocomplete="off" placeholder="${config.has_api_key?'Key saved. Leave blank to keep it.':'API key'}"><label><input id="clear-key" type="checkbox"> Remove saved API key</label><label for="upload-url">Upload endpoint</label><input id="upload-url" type="url" value="${escapeHTML(config.upload_url)}" placeholder="HTTPS upload endpoint"></section>
    <div class="full-width">${previewHTML('settings-mask')}</div>
  </div><div id="error" class="error-box" hidden></div><div class="actions"><button class="secondary" type="button" id="cancel-settings">Cancel</button><span class="hint">Settings are saved on this device.</span><button id="save-settings">Save settings</button></div></form>`;
  $('#back-dashboard').onclick=dashboard; $('#cancel-settings').onclick=dashboard;
  settingsSourceField(); setupMaskEditor(); startPreview(); $('#settings-source-type').onchange=()=>{sourceType=$('#settings-source-type').value;settingsSourceField();};
  mountMap(); bindLocationControls();
  $('#settings-form').onsubmit=async event=>{event.preventDefault();const button=$('#save-settings');button.disabled=true;$('#error').hidden=true;try{const [latitude,longitude]=coordinates();const source=$('#settings-source').value.trim();if(!source&&sourceType!==config.source_type)throw new Error('Enter a camera address or device.');if(detectionMask.length>0&&detectionMask.length<3)throw new Error('Add at least three points for a detection area, or clear it to use the full image.');config=await api('config','PATCH',{name:$('#settings-name').value.trim(),source_type:sourceType,source:source||null,detection_mask:detectionMask,latitude,longitude,bearing:+$('#bearing').value,field_of_view:+$('#fov').value,confidence:+$('#confidence').value,confirmations:+$('#confirmations').value,pre_roll:+$('#pre-roll').value,post_roll:+$('#post-roll').value,max_clip_seconds:+$('#max-clip').value,storage_gb:+$('#storage').value,api_key:$('#clear-key').checked?'':($('#api-key').value.trim()||null),upload_url:$('#upload-url').value.trim()});toast('Settings saved.');settingsPage();}catch(e){error(e.message);}finally{if(button.isConnected)button.disabled=false;}};
}
async function settingsSourceField() {
  const isRtsp=sourceType==='rtsp';
  $('#settings-source-field').innerHTML=isRtsp?`<label for="settings-source">RTSP stream URL</label><input id="settings-source" type="text" autocomplete="off" spellcheck="false" placeholder="${config.source_type==='rtsp'?'Saved stream. Leave blank to keep it.':'rtsp://camera/stream'}"><p class="hint">${config.source_type==='rtsp'?`Current stream: ${escapeHTML(config.source_label)}. Enter a new address to replace it.`:'Stream credentials are stored on this device.'}</p>`:`<label for="settings-source">Camera device</label><input id="settings-source" list="settings-devices" value="${sourceType===config.source_type&&config.source_type==='usb'?escapeHTML(config.source):'/dev/video0'}"><datalist id="settings-devices"></datalist><p class="hint" id="settings-device-hint">Checking for connected cameras…</p>`;
  if(!isRtsp)try{const devices=await api('devices');if(!$('#settings-devices'))return;$('#settings-devices').innerHTML=devices.map(d=>`<option value="${escapeHTML(d.path)}">${escapeHTML(d.label)}</option>`).join('');$('#settings-device-hint').textContent=devices.length?`${devices.length} video device(s) found.`:'No USB cameras detected. Enter a /dev/video path.';}catch(e){error(e.message);}
}
async function refreshSightings() {
  const rows=await api(`sightings?offset=${offset}`);if(page!=='dashboard')return;
  $('#previous').hidden=offset===0;$('#more').hidden=rows.length<50;
  $('#sightings').innerHTML=rows.length?rows.map(row=>`<article class="event-item"><div class="event-item-top"><strong>${new Date(row.started*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</strong><span class="badge ${row.status}">${row.status==='pending'?'Queued':escapeHTML(row.status)}</span></div><small>${row.ended?`${Math.max(0,Math.round(row.ended-row.started))}s recorded`:'Recording in progress'} · ${row.manual_review?'Manually marked':`${Math.round(row.confidence*100)}% confidence`}</small>${row.error?`<small class="error">${escapeHTML(row.error)}</small>`:''}<div class="event-actions">${row.video_url?`<button class="secondary play-video" data-url="${escapeHTML(row.video_url)}">View clip</button>`:`<span class="hint">${row.status==='uploaded'?'Clip removed':'Stored on device'}</span>`}${row.status==='pending'&&row.attempts?`<button class="text-button retry" data-id="${row.id}">Retry upload</button>`:''}</div></article>`).join(''):`<div class="empty-state"><strong>No sightings recorded</strong><p>Detected train events and their video clips will appear here.</p></div>`;
  document.querySelectorAll('.play-video').forEach(button=>button.onclick=()=>{const video=$('#video-dialog video');video.src=button.dataset.url;$('#video-dialog').showModal();video.play().catch(()=>{});});
  document.querySelectorAll('.retry').forEach(button=>button.onclick=async()=>{try{await api(`sightings/${button.dataset.id}/retry`,'POST');toast('Upload queued for retry');}catch(e){error(e.message);}});
}
async function refreshDashboard() {
  try {
    const s=await api('status');if(page!=='dashboard')return;
    $('#stat-total').textContent=s.sightings.total;$('#event-count').textContent=s.sightings.total;$('#stat-uploaded').textContent=s.sightings.counts.uploaded||0;$('#stat-pending').textContent=(s.sightings.counts.pending||0)+(s.sightings.counts.uploading||0);$('#stat-storage').textContent=`${(s.sightings.bytes/1e9).toFixed(2)} GB`;
    $('#camera-status').textContent=s.camera;$('#detector-status').textContent=s.detector;$('#buffer-status').textContent=s.footage_seconds?`${(s.footage_seconds/3600).toFixed(1)} hours available · 24h retention`:'Building first segment';$('#upload-status').textContent=s.uploads;const watchBadge=$('#watch-badge'); const state=s.recording?'Recording':!s.enabled?'Paused':s.detector==='watching'?'Monitoring':'Check system'; watchBadge.lastChild.textContent=state; watchBadge.dataset.state=state.toLowerCase().replaceAll(' ','-');
    $('#storage-error').hidden=!s.storage_error;$('#storage-error').textContent=s.storage_error||'';await refreshSightings();
  }catch(e){if(page==='dashboard')toast(e.message);}
  if(page==='dashboard')statusTimer=setTimeout(refreshDashboard,5000);
}
$('#close-video').onclick=()=>$('#video-dialog').close();$('#video-dialog').addEventListener('close',()=>{$('#video-dialog video').pause();$('#video-dialog video').removeAttribute('src');$('#video-dialog video').load();});
boot();
