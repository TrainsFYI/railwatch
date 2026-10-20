const $ = (q) => document.querySelector(q);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config, step = 0, page = 'setup', map, marker, wedge, previewTimer, statusTimer, offset = 0;
let sourceType = 'usb', locationChosen = false, detectionMask = [];
let landmarks = [], selectedLandmark = '', landmarkDrag = null;
// Keep the setup order aligned with the review summary and saveStep.
const stepNames = ['Camera', 'Location', 'Rail landmarks', 'Train area', 'Account', 'Review'];
let dashboardView = 'overview';
function navigation(active) {
  const sidebar = $('#app-sidebar');
  sidebar.innerHTML = `<p class="nav-caption">${config.setup_complete ? 'WORKSPACE' : 'GETTING STARTED'}</p><nav>${(config.setup_complete ? [['overview','Live'],['detections','Detections'],['settings','Settings']] : [['setup','Camera setup']]).map(([id,label])=>`<button type="button" data-page="${id}" aria-current="${active===id?'page':'false'}">${label}</button>`).join('')}</nav><div class="sidebar-note"><a href="https://trains.fyi" target="_blank" rel="noopener noreferrer">Trains.fyi <span aria-hidden="true">↗</span></a></div>`;
  sidebar.querySelectorAll('[data-page]').forEach(button=>button.onclick=()=>{
    const target=button.dataset.page;
    if(target==='settings')settingsPage();else if(target==='setup')setup(0);else dashboard(target);
  });
}
function setupProgress(index) {
  return `<ol class="steps" aria-label="Setup progress">${stepNames.map((name,i)=>`<li class="step ${i===index?'active':''}" ${i===index?'aria-current="step"':''}><b>${i<index?'✓':i+1}</b>${name}</li>`).join('')}</ol>`;
}
function applyTheme(theme) {
  document.documentElement.dataset.theme=theme;
  try { localStorage.setItem('railwatcher-theme',theme); } catch {}
  $('meta[name="theme-color"]').content=theme==='dark'?'#151517':'#ffffff';
}
$('meta[name="theme-color"]').content=document.documentElement.dataset.theme==='dark'?'#151517':'#ffffff';
async function refreshSystemStatus() {
  const badge=$('#system-status'),label=$('#system-status-label');
  try {
    const status=await api('status');
    const state=status.storage_error?'Needs attention':!status.enabled?'Paused':status.camera!=='connected'?'Camera disconnected':!status.model_available?'Model unavailable':status.recording?'Recording':status.detector==='watching'?'Monitoring':'Starting';
    label.textContent=state;badge.dataset.state=state.toLowerCase().replaceAll(' ','-');
    badge.title=`Camera: ${status.camera} · Detection: ${status.detector} · Uploads: ${status.uploads}`;
  } catch { label.textContent='Disconnected';badge.dataset.state='disconnected';badge.title='Cannot reach RailWatch'; }
  setTimeout(refreshSystemStatus,5000);
}
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
    if (config.setup_complete) dashboard();
    else { let resume=0; try { resume=Number(sessionStorage.getItem('railwatcher-setup-step'))||0; } catch {} setup(Number.isInteger(resume)?Math.min(5,Math.max(0,resume)):0); }
  } catch(e) {
    $('#app').innerHTML = `<section class="card"><h1>RailWatch is unavailable</h1><p>${escapeHTML(e.message)}</p><button id="reload">Retry</button></section>`;
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
  const boxes = page === 'settings' ? landmarks : (config.landmarks || []);
  for (const landmark of boxes) {
    const [left, top, right, bottom] = landmark.box;
    const x = left * rect.width, y = top * rect.height;
    context.strokeStyle = landmark.id === selectedLandmark && page === 'settings' ? '#ffcf40' : '#57dbff';
    context.fillStyle = 'rgba(87, 219, 255, .12)';
    context.fillRect(x, y, (right-left)*rect.width, (bottom-top)*rect.height);
    context.lineWidth = 2; context.strokeRect(x, y, (right-left)*rect.width, (bottom-top)*rect.height);
    context.font = '12px sans-serif';
    const width = Math.min(rect.width-x, context.measureText(landmark.label).width+8);
    context.fillStyle = '#142033'; context.fillRect(x, Math.max(0,y-19), width, 19);
    context.fillStyle = '#fff'; context.fillText(landmark.label,x+4,Math.max(14,y-5),Math.max(1,width-8));
  }
  if (page === 'calibration') {
    const previewPoints=railPoints.map((point,index)=>({...point,index})).filter(point=>point.index!==railDraft?.index);
    if(railDraft?.image)previewPoints.push(railDraft);
    previewPoints.forEach((point) => {
      const index=point.index;
      if (!point.image) return;
      const [x,y] = point.image.map((value,i)=>value*(i?rect.height:rect.width));
      context.fillStyle='#ffcf40'; context.beginPath(); context.arc(x,y,7,0,Math.PI*2); context.fill();
      context.font='bold 16px sans-serif'; context.fillText('ABC'[index],x+10,y-8);
    });
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
    if ($('#camera-edit-mode')?.value === 'landmarks') return;
    if (!$('#preview-image')?.naturalWidth || $('#preview-image').hidden) return toast('Wait for a camera picture before drawing the train area.');
    if (detectionMask.length >= 32) return toast('Detection areas can have up to 32 points.');
    const rect = canvas.getBoundingClientRect();
    detectionMask = [...detectionMask, [
      Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    ]];
    if($('#full-frame')) { $('#full-frame').checked=false; if(detectionMask.length>=3) $('#error').hidden=true; }
    drawMask();
  });
  $('#mask-undo').onclick = () => { detectionMask = detectionMask.slice(0, -1); drawMask(); };
  $('#mask-clear').onclick = () => { detectionMask = []; drawMask(); };
  window.onresize = drawMask;
  drawMask();
}
function landmarkEditorHTML() {
  return `<div class="landmark-editor"><h3>Highlightable landmarks</h3><p>Label fixed features such as crossing lights or a gate. Select a landmark, then drag a box around it. Drag again to replace its box.</p><label for="camera-edit-mode">Image editing mode</label><select id="camera-edit-mode"><option value="detection">Detection area</option><option value="landmarks">Landmark boxes</option></select><div class="landmark-controls"><div><label for="landmark-select">Selected landmark</label><select id="landmark-select"></select></div><button type="button" class="secondary" id="landmark-add">Add landmark</button></div><label for="landmark-label">Landmark label</label><input id="landmark-label" maxlength="80" placeholder="e.g. Crossing light, left"><button type="button" class="text-button" id="landmark-remove">Remove selected landmark</button><p class="hint">Save settings to keep your boxes and labels.</p></div>`;
}
function setupLandmarkEditor() {
  const select = $('#landmark-select'), label = $('#landmark-label'), canvas = $('#mask-canvas');
  const current = () => landmarks.find(item => item.id === selectedLandmark);
  function refresh() {
    select.innerHTML = landmarks.length ? landmarks.map(item=>`<option value="${escapeHTML(item.id)}">${escapeHTML(item.label || 'Unnamed landmark')}</option>`).join('') : '<option value="">No landmarks yet</option>';
    select.value = selectedLandmark; select.disabled = !landmarks.length;
    label.value = current()?.label || ''; label.disabled = !current();
    $('#landmark-remove').disabled = !current(); $('#landmark-add').disabled = landmarks.length >= 32;
    drawMask();
  }
  select.onchange = () => { selectedLandmark = select.value; $('#camera-edit-mode').value = 'landmarks'; refresh(); };
  label.oninput = () => { if(current()){current().label = label.value; select.selectedOptions[0].textContent=label.value || 'Unnamed landmark'; drawMask();} };
  $('#landmark-add').onclick = () => {
    if(landmarks.length >= 32) return;
    selectedLandmark = `landmark-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
    landmarks.push({id:selectedLandmark,label:`Landmark ${landmarks.length+1}`,box:[.4,.4,.6,.6]});
    $('#camera-edit-mode').value = 'landmarks'; refresh(); label.focus(); label.select();
  };
  $('#landmark-remove').onclick = () => { landmarks = landmarks.filter(item=>item.id!==selectedLandmark); selectedLandmark=landmarks[0]?.id || ''; refresh(); };
  $('#camera-edit-mode').onchange = () => { landmarkDrag = null; drawMask(); };
  const point = event => { const rect=canvas.getBoundingClientRect(); return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))]; };
  canvas.onpointerdown = event => {
    if($('#camera-edit-mode').value !== 'landmarks') return;
    if(!current()) return toast('Add a landmark first.');
    if(event.button !== 0) return;
    event.preventDefault(); landmarkDrag={start:point(event),original:[...current().box],id:selectedLandmark}; canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove = event => {
    if(!landmarkDrag) return;
    const [x,y]=point(event),[sx,sy]=landmarkDrag.start;
    current().box=[Math.min(x,sx),Math.min(y,sy),Math.max(x,sx),Math.max(y,sy)]; drawMask();
  };
  const finish = cancelled => {
    if(!landmarkDrag) return;
    const item=landmarks.find(item=>item.id===landmarkDrag.id);
    if(item && (cancelled || item.box[2]-item.box[0]<.003 || item.box[3]-item.box[1]<.003)) item.box=landmarkDrag.original;
    landmarkDrag=null; drawMask();
  };
  canvas.onpointerup = () => finish(false); canvas.onpointercancel = () => finish(true);
  refresh();
}
function startPreview() {
  clearTimeout(previewTimer);
  bindFullscreen();
  const image = $('#preview-image');
  if (!image) return;
  image.onload=()=>{
    if(image.naturalWidth && !document.fullscreenElement){
      const viewer=image.closest('.preview');
      viewer.style.flex='none';viewer.style.minHeight='0';
      viewer.style.aspectRatio=`${image.naturalWidth} / ${image.naturalHeight}`;
    }
    drawMask();
  };
  const tick = async () => {
    if (!image.isConnected) return;
    if(page==='settings'&&!['camera','detection'].includes(settingsSection)){previewTimer=setTimeout(tick,1000);return;}
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
  cleanup(); page = 'setup'; step = index; $('#app').dataset.view='';
  navigation('setup');
  try { sessionStorage.setItem('railwatcher-setup-step', String(index)); } catch {}
  if(index===2) { calibrationPage(()=>setup(3), true); return; }
  const subtitles = ['Connect your camera', 'Place your camera', 'Rail landmarks', 'Outline the train area', 'Connect your account', 'Ready to watch'];
  const descriptions = ['Choose a video source, save the connection, and check the live picture.', 'Find the camera on the map, then aim its viewing cone toward the tracks.', '', 'Draw around the rails and the full space a passing train will occupy.', 'Optionally connect to TrainsFYI for private uploads.', 'Check your camera, rail landmarks, and detection area before starting.'];
  $('#app').innerHTML = `<div class="intro"><div><h1>${subtitles[step]}</h1><p>${descriptions[step]}</p></div></div>${setupProgress(step)}<div id="step-content"></div><div id="error" class="error-box" hidden></div><div class="actions"><button class="secondary" id="back">${step ? '← Back' : config.setup_complete ? '← Dashboard' : 'Back'}</button><span class="hint">${step===5?'The live camera feed stays on this device.':'Changes are saved on this device.'}</span><button id="next">${step===5?'Start watching':'Continue'} &nbsp; →</button></div>`;
  $('#back').disabled = step===0 && !config.setup_complete;
  $('#back').onclick = () => step ? setup(step-1) : dashboard();
  $('#next').onclick = async () => {
    const button = $('#next'); button.disabled = true; $('#error').hidden = true;
    try { await saveStep(); if (step===5) dashboard(); else setup(step+1); } catch(e) { error(e.message); } finally { if(button.isConnected) button.disabled = false; }
  };
  [cameraStep, locationStep, null, areaStep, accountStep, reviewStep][step]();
}
function cameraStep() {
  $('#step-content').innerHTML = `<div class="setup-grid"><section class="card"><h2>Video source</h2><p>Select a USB camera or enter the address of a network stream.</p><label for="name">Camera name</label><input id="name" maxlength="80" value="${escapeHTML(config.name)}" placeholder="e.g. West junction"><div class="camera-choices"><button class="camera-choice" id="usb"><span class="camera-symbol">USB</span>USB camera<span>Connected to this device</span></button><button class="camera-choice" id="rtsp"><span class="camera-symbol">RTSP</span>Network stream<span>RTSP address on your network</span></button></div><div id="camera-input"></div><button class="secondary" id="test-camera" style="margin-top:20px">Save camera settings</button><div class="note">The live camera feed is processed on this device.</div></section>${previewHTML('',false)}</div>`;
  $('#usb').onclick = () => cameraInput('usb'); $('#rtsp').onclick = () => cameraInput('rtsp'); cameraInput(sourceType);
  $('#test-camera').onclick = async () => { const button = $('#test-camera'); button.disabled = true; try { await saveCamera(); startPreview(); toast('Camera settings saved.'); } catch(e) { error(e.message); } finally {button.disabled = false;} };
  if (config.setup_complete || config.source_type==='rtsp') startPreview();
}
async function cameraInput(type) {
  sourceType = type; $('#usb').classList.toggle('selected', type==='usb'); $('#rtsp').classList.toggle('selected', type==='rtsp');
  $('#usb').setAttribute('aria-pressed', type==='usb'); $('#rtsp').setAttribute('aria-pressed', type==='rtsp');
  if(type==='rtsp') {
    $('#camera-input').innerHTML = `<label for="source">RTSP stream URL</label><input id="source" type="text" autocomplete="off" spellcheck="false" placeholder="rtsp://username:password@192.168.1.20:554/stream"><p class="hint">${config.source_type==='rtsp' ? `Saved camera: ${escapeHTML(config.source_label)}. Leave blank to keep it.` : 'Find this address in your camera’s settings. Credentials are stored only on this device.'}</p>`;
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
    if(locationChosen) position(config.latitude, config.longitude, true);
  } else $('#map-message').textContent = 'Map library unavailable. Enter coordinates to continue.';
}
function bindLocationControls() {
  $('#go-coordinates').onclick = () => {try {const [lat,lon] = coordinates(); position(lat,lon,true); if(map) map.setView([lat,lon],18);}catch(e){error(e.message);}};
  $('#latitude').oninput = $('#longitude').oninput = () => {locationChosen=false;};
  $('#locate').onclick = () => { if(!navigator.geolocation) return error('Location is unavailable. Enter coordinates or click the map.'); navigator.geolocation.getCurrentPosition(p => {position(p.coords.latitude,p.coords.longitude); if(map) map.setView([p.coords.latitude,p.coords.longitude],18);},()=>error('Location access is unavailable here. Click the map or enter coordinates.'),{timeout:10000}); };
  $('#bearing').oninput = () => {$('#bearing-value').textContent = `${$('#bearing').value}° · ${compass(+$('#bearing').value)}`; drawCone();};
  $('#fov').oninput = () => {$('#fov-value').textContent = `${$('#fov').value}°`; drawCone();};
}
function coordinates() {const a=$('#latitude').value,b=$('#longitude').value; const lat=Number(a),lon=Number(b);if(!a||!b||!Number.isFinite(lat)||!Number.isFinite(lon)||Math.abs(lat)>85||Math.abs(lon)>180) throw new Error('Choose a location on the map or enter valid latitude and longitude.');return [lat,lon];}
function compass(b) {return ['N','NE','E','SE','S','SW','W','NW'][Math.round(b/45)%8];}
function position(lat,lon,preservePrecision=false) {
  const originalLongitude=lon;
  lon = ((lon+180)%360+360)%360-180; if(Math.abs(lat)>85) return;
  locationChosen = true; $('#latitude').value=preservePrecision?String(lat):lat.toFixed(6);$('#longitude').value=preservePrecision?String(originalLongitude):lon.toFixed(6);
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
function areaStep() {
  $('#step-content').innerHTML = `<div class="setup-grid"><section class="card"><h2>Keep the focus on trains</h2><p>Click around the outside of the visible tracks to draw a closed area. Include the height and width of a passing train, from where it enters the picture to where it leaves.</p><ol class="area-guide"><li>Start beside the rail at the near end.</li><li>Follow the track toward the far end.</li><li>Come back along the other side, leaving room above the rails for the train.</li></ol><p>Use at least three points. Undo the last point to adjust your outline.</p><label><input type="checkbox" id="full-frame" > Use the whole image instead</label><p class="hint">Choose this only when the whole camera view is relevant to train detection.</p></section>${previewHTML()}</div>`;
  setupMaskEditor(); startPreview();
  $('#full-frame').onchange=()=>{if($('#full-frame').checked){detectionMask=[];drawMask();}};
}
function accountStep() {
  $('#step-content').innerHTML=`<div class="setup-grid"><section class="card"><h2>TrainsFYI account</h2><p>Authorize this camera in your browser. Confirmed train passages upload automatically; footage stays private.</p><button type="button" id="pair-device">Connect to TrainsFYI</button><div id="device-pairing" aria-live="polite"></div><div hidden><input id="api-key"><input id="clear-key" type="checkbox"><input id="upload-url" value="${escapeHTML(config.upload_url)}"></div></section><section class="setup-aside"><h3>What is uploaded</h3><p>Only recorded train clips and camera location details are sent. Live video stays on this device.</p><p>If the network is unavailable, clips remain queued here for a later retry.</p></section></div>`;
  bindDevicePairing();
}
function reviewStep() {
  $('#step-content').innerHTML=`<div class="setup-grid"><section class="card"><h2>Review settings</h2>${[['Name',config.name],['Camera',config.source_label],['Position',`${config.latitude?.toFixed(6)}, ${config.longitude?.toFixed(6)}`],['Direction',`${config.bearing}° ${compass(config.bearing)} · ${config.field_of_view}° view`],['Rail landmarks',`${config.railway_calibration?.points?.length || 0} matched points`],['Train area',detectionMask.length ? `${detectionMask.length}-point outline` : 'Whole image'],['TrainsFYI','Manage account connection in Settings']].map(([a,b])=>`<div class="review-item"><span>${a}</span><strong>${escapeHTML(b)}</strong></div>`).join('')}<div class="inline-inputs"><div><label for="confidence">Detection confidence</label><input id="confidence" type="number" min="0.1" max="0.99" step="0.05" value="${config.confidence}"></div><div><label for="storage">Recording storage (GB)</label><input id="storage" type="number" min="1" max="1000" step="0.25" value="${config.storage_gb}"></div></div><p class="hint">${config.pre_roll}s before detection · ${config.post_roll}s after the last detection. Long trains are saved in consecutive clips.</p><div id="model-note" class="note">Checking the local detector…</div></section>${previewHTML('',false)}</div>`;
  startPreview(); api('status').then(s=>{const note=$('#model-note');if(!note)return;note.textContent=s.model_available?'Train model installed. Detection runs continuously. Use infrared or low-light equipment at night.':'Train model is not installed. Add models/train.onnx to enable detection.';}).catch(e=>error(e.message));
}
async function saveStep() {
  if(step===0) return saveCamera();
  if(step===1) {const [latitude,longitude]=coordinates(); config=await api('config','PATCH',{latitude,longitude,bearing:+$('#bearing').value,field_of_view:+$('#fov').value});}
  if(step===3) {
    if(!$('#full-frame').checked && detectionMask.length<3) throw new Error('Outline the train area with at least three points, or choose the whole image.');
    config=await api('config','PATCH',{detection_mask:detectionMask});
  }
  if(step===4) config=await api('config','PATCH',{api_key:$('#clear-key').checked?'':($('#api-key').value.trim()||null),upload_url:$('#upload-url').value.trim()});
  if(step===5 && (config.railway_calibration?.points?.length || 0)<2) throw new Error('Match two rail landmarks before starting. Go back to the Rail landmarks step.');
  if(step===5) config=await api('config','PATCH',{confidence:+$('#confidence').value,storage_gb:+$('#storage').value,setup_complete:true,enabled:true});
}
function dashboard(view = 'overview') {
  dashboardView=typeof view==='string'?view:'overview'; navigation(dashboardView);
  $('#app').dataset.view=dashboardView;
  cleanup(); page='dashboard';offset=0;
  $('#app').innerHTML=`<div class="dashboard-layout"><aside class="dashboard-sidebar">${previewHTML('sidebar-preview',false)}<section class="card event-panel"><div class="card-head"><div><h2>${dashboardView==='detections'?'Detections':'Recent events'}</h2><p>${dashboardView==='detections'?'Recorded events and detections needing your review':'The latest train activity from your camera'}</p></div><span class="event-count" id="event-count">—</span></div><div id="sightings" class="event-list"></div><div class="event-pagination"><button id="previous" class="text-button" hidden>← Newer</button><button id="more" class="text-button" hidden>Older →</button></div></section></aside><section class="dashboard-main"><div class="intro"><div><h1>${escapeHTML(config.name)}</h1><p>${config.latitude === null ? 'Location not set' : `${config.latitude.toFixed(5)}, ${config.longitude.toFixed(5)}`} · Facing ${config.bearing}° ${compass(config.bearing)}</p></div><div class="dashboard-actions"><button class="secondary" id="settings">Camera settings</button></div></div><div class="stats"><div class="stat"><div class="stat-label">Sightings</div><div class="stat-value" id="stat-total">—</div><div class="stat-detail">recorded events</div></div><div class="stat"><div class="stat-label">Uploaded</div><div class="stat-value" id="stat-uploaded">—</div><div class="stat-detail">accepted by server</div></div><div class="stat"><div class="stat-label">Queued</div><div class="stat-value" id="stat-pending">—</div><div class="stat-detail">stored on device</div></div><div class="stat"><div class="stat-label">Clip storage</div><div class="stat-value" id="stat-storage">—</div><div class="stat-detail">of ${config.storage_gb} GB limit</div></div></div><section class="card system-card"><div class="card-head"><h2>System status</h2><span class="watch-state" id="watch-badge"><span class="watch-indicator" aria-hidden="true"></span>Starting</span></div><div class="status-list"><div class="status-row"><span>Camera</span><strong id="camera-status">—</strong></div><div class="status-row"><span>Detection</span><strong id="detector-status">—</strong></div><div class="status-row"><span>Footage archive</span><strong id="buffer-status">—</strong></div><div class="status-row"><span>TrainsFYI</span><strong id="upload-status">—</strong></div><div class="status-row"><span>Facing</span><strong>${config.bearing}° ${compass(config.bearing)}</strong></div></div><div id="storage-error" class="error-box" hidden></div><button class="secondary" id="toggle" style="width:100%;margin-top:16px">${config.enabled?'Pause detection':'Start detection'}</button></section></section></div>`;
  $('#settings').onclick=()=>settingsPage();
  $('#toggle').onclick=async()=>{try{config=await api('config','PATCH',{enabled:!config.enabled});$('#toggle').textContent=config.enabled?'Pause detection':'Start detection';toast(config.enabled?'Train detection enabled':'Train detection paused');}catch(e){error(e.message);}};
  $('#more').onclick=()=>{offset+=50;refreshSightings();};$('#previous').onclick=()=>{offset=Math.max(0,offset-50);refreshSightings();};
  startPreview(); refreshDashboard();
}
let settingsSection = 'camera';
function settingsPage(section = settingsSection) {
  settingsSection=typeof section==='string'?section:settingsSection;
  cleanup(); page='settings'; navigation('settings'); $('#app').dataset.view=''; locationChosen=config.latitude!==null&&config.longitude!==null; sourceType=config.source_type;
  detectionMask = (config.detection_mask || []).map(point => [...point]);
  landmarks = (config.landmarks || []).map(item=>({...item,box:[...item.box]}));
  selectedLandmark=landmarks[0]?.id || ''; landmarkDrag=null;
  $('#app').innerHTML=`<div class="intro"><div><h1>Settings</h1><p>Camera, location, detection and upload configuration.</p></div><button class="secondary" id="back-dashboard">Back to dashboard</button></div><form id="settings-form"><div class="settings-grid">
    <section class="card"><h2>Camera</h2><label for="settings-name">Camera name</label><input id="settings-name" maxlength="80" required value="${escapeHTML(config.name)}"><label for="settings-source-type">Video source</label><select id="settings-source-type"><option value="usb" ${sourceType==='usb'?'selected':''}>USB camera</option><option value="rtsp" ${sourceType==='rtsp'?'selected':''}>RTSP network stream</option></select><div id="settings-source-field"></div></section>
    <section class="card"><h2>Location and direction</h2><button type="button" class="secondary" id="rail-calibration">Match railway points</button><p>Place the camera on the map. Railway lines are shown over satellite imagery.</p><div class="inline-inputs"><div><label for="latitude">Latitude</label><input id="latitude" type="number" step="any" min="-85" max="85" value="${config.latitude??''}" required></div><div><label for="longitude">Longitude</label><input id="longitude" type="number" step="any" min="-180" max="180" value="${config.longitude??''}" required></div></div><div class="map-controls"><button class="secondary" type="button" id="go-coordinates">Show coordinates</button><button class="secondary" type="button" id="locate">Use my location</button></div><label class="bearing-label" for="bearing"><span>Camera direction</span><span id="bearing-value">${config.bearing}° · ${compass(config.bearing)}</span></label><input id="bearing" type="range" min="0" max="359" value="${config.bearing}"><label class="bearing-label" for="fov"><span>Field of view</span><span id="fov-value">${config.field_of_view}°</span></label><input id="fov" type="range" min="10" max="160" value="${config.field_of_view}"></section>
    <section class="card settings-map"><div id="map"></div><p class="map-note" id="map-message">Click the map to place the camera. Drag to adjust.</p></section>
    <section class="card"><h2>Detection and storage</h2><div class="inline-inputs"><div><label for="confidence">Detection confidence</label><input id="confidence" type="number" min="0.1" max="0.99" step="0.05" value="${config.confidence}"></div><div><label for="confirmations">Confirmations within 5 seconds</label><input id="confirmations" type="number" min="1" max="10" step="1" value="${config.confirmations}"></div></div><div class="inline-inputs"><div><label for="pre-roll">Pre-roll (seconds)</label><input id="pre-roll" type="number" min="3" max="120" value="${config.pre_roll}"></div><div><label for="post-roll">Post-roll (seconds)</label><input id="post-roll" type="number" min="3" max="60" value="${config.post_roll}"></div></div><div class="inline-inputs"><div><label for="max-clip">Maximum clip (seconds)</label><input id="max-clip" type="number" min="30" max="900" value="${config.max_clip_seconds}"></div><div><label for="storage">Storage limit (GB)</label><input id="storage" type="number" min="1" max="1000" step="0.25" value="${config.storage_gb}"></div></div></section>
    <section class="card" id="night-review-settings"><h2>Night review</h2><label><input type="checkbox" id="night-review-enabled" ${config.night_review_enabled?'checked':''}> Retain nighttime footage for local review</label><p class="hint">Keep extra footage outside the rolling 24-hour archive. This does not automatically upload candidates. Retention limits apply to these recordings.</p><label for="night-review-timezone">Timezone</label><input id="night-review-timezone" value="${escapeHTML(config.night_review_timezone||'UTC')}" placeholder="America/Toronto"><div class="inline-inputs"><div><label for="night-start">Start hour (0–23)</label><input id="night-start" type="number" min="0" max="23" value="${config.night_review_start_hour??19}"></div><div><label for="night-end">End hour (0–23)</label><input id="night-end" type="number" min="0" max="23" value="${config.night_review_end_hour??7}"></div></div><div class="inline-inputs"><div><label for="night-hours">Retention (hours)</label><input id="night-hours" type="number" min="24" max="168" value="${config.night_review_hours??72}"></div><div><label for="night-gb">Extra storage budget (GB)</label><input id="night-gb" type="number" min="0.25" max="20" step="0.25" value="${config.night_review_gb??2}"></div></div><p class="hint">Oldest review footage expires when either limit is reached. Pending uploads are preserved independently.</p></section>
    <section class="card"><h2>TrainsFYI account</h2><p>Authorize this camera in your browser. Confirmed train passages upload automatically; footage stays private.</p><button type="button" id="pair-device">Connect to TrainsFYI</button><div id="device-pairing" aria-live="polite"></div><div hidden><input id="api-key"><input id="clear-key" type="checkbox"><input id="upload-url" value="${escapeHTML(config.upload_url)}"></div></section>
    <div class="full-width">${previewHTML('settings-mask')}${landmarkEditorHTML()}</div>
  </div><div id="error" class="error-box" hidden></div><div class="actions"><button class="secondary" type="button" id="cancel-settings">Cancel</button><span class="hint">Settings are saved on this device.</span><button id="save-settings">Save settings</button></div></form>`;
  const nightCard=$('#night-review-settings');nightCard.previousElementSibling.append(nightCard);
  buildSettingsSections();
  $('#back-dashboard').onclick=dashboard; $('#cancel-settings').onclick=dashboard;
  $('#rail-calibration').onclick=()=>calibrationPage(()=>settingsPage('railway'));
  bindDevicePairing();
  settingsSourceField(); setupMaskEditor(); setupLandmarkEditor(); startPreview(); $('#settings-source-type').onchange=()=>{sourceType=$('#settings-source-type').value;settingsSourceField();};
  mountMap(); bindLocationControls();
  window.onresize=()=>{drawMask();scrollSettingsTabIntoView();if(map&&settingsSection==='location')map.invalidateSize();};
  $('#settings-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;if(!form.checkValidity()){const invalid=form.querySelector(':invalid');showSettingsSection(invalid.closest('[data-settings-panel]').dataset.settingsPanel);invalid.reportValidity();return;}const button=$('#save-settings');button.disabled=true;$('#error').hidden=true;try{const [latitude,longitude]=coordinates();const source=$('#settings-source').value.trim();if(!source&&sourceType!==config.source_type)throw new Error('Enter a camera address or device.');if(detectionMask.length>0&&detectionMask.length<3)throw new Error('Add at least three points for a detection area, or clear it to use the full image.');config=await api('config','PATCH',{name:$('#settings-name').value.trim(),source_type:sourceType,source:source||null,detection_mask:detectionMask,landmarks,approximate_speed_enabled:$('#approximate-speed').checked,latitude,longitude,bearing:+$('#bearing').value,field_of_view:+$('#fov').value,confidence:+$('#confidence').value,confirmations:+$('#confirmations').value,pre_roll:+$('#pre-roll').value,post_roll:+$('#post-roll').value,max_clip_seconds:+$('#max-clip').value,storage_gb:+$('#storage').value,night_review_enabled:$('#night-review-enabled').checked,night_review_timezone:$('#night-review-timezone').value.trim(),night_review_start_hour:+$('#night-start').value,night_review_end_hour:+$('#night-end').value,night_review_hours:+$('#night-hours').value,night_review_gb:+$('#night-gb').value,api_key:$('#clear-key').checked?'':($('#api-key').value.trim()||null),upload_url:$('#upload-url').value.trim()});toast('Settings saved.');settingsPage();}catch(e){error(e.message);}finally{if(button.isConnected)button.disabled=false;}};
}

function buildSettingsSections() {
  const form=$('#settings-form');form.noValidate=true;
  const old=form.querySelector('.settings-grid');
  const [camera,location,mapCard,recording,account,visual]=[...old.children];
  const nightReview=recording.querySelector('#night-review-settings');
  const confidence=recording.querySelector('.inline-inputs');
  const preview=visual.querySelector('.preview-card');
  const landmarks=visual.querySelector('.landmark-editor');
  const match=$('#rail-calibration');match.remove();
  location.querySelector('h2').textContent='Camera position';
  recording.querySelector('h2').remove();
  const padding=$('#pre-roll').closest('.inline-inputs');
  const maximum=$('#max-clip').parentElement, storage=$('#storage').parentElement;
  const captureGroup=document.createElement('div');captureGroup.className='settings-group';captureGroup.innerHTML='<h3>Clip capture</h3>';captureGroup.append(padding,maximum);
  const storageGroup=document.createElement('div');storageGroup.innerHTML='<h3>Local storage</h3><p>Set the space available for stored clips.</p>';storageGroup.append(storage);
  recording.replaceChildren(captureGroup,storageGroup);
  if(nightReview)recording.append(nightReview);
  camera.querySelector('h2').outerHTML='<h3>Camera connection</h3>';
  location.querySelector('h2').outerHTML='<h3>Camera position</h3>';
  const panel=(id,title,description)=>{const element=document.createElement('section');element.id='settings-panel-'+id;element.dataset.settingsPanel=id;element.className='settings-panel';element.setAttribute('role','tabpanel');element.setAttribute('aria-labelledby','settings-tab-'+id);element.innerHTML=`<div class="settings-panel-heading"><h2>${title}</h2><p>${description}</p></div>`;return element;};
  const panels={
    appearance:panel('appearance','Appearance','Choose how RailWatch looks on this browser.'),
    camera:panel('camera','Camera','Name your camera and manage its video connection.'),
    location:panel('location','Location & view','Set where the camera is installed and the view it covers.'),
    railway:panel('railway','Railway calibration','Match fixed points in the camera image with the same points on the map.'),
    detection:panel('detection','Train detection','Choose the track area, detector sensitivity, and labeled landmarks.'),
    recording:panel('recording','Recording & storage','Control clip padding, recording length, and local storage.'),
    models:panel('models','Models & local analysis','Download current model weights and inspect installed versions.'),
    account:panel('account','TrainsFYI account','Connect this device to your account for private uploads.')
  };
  panels.appearance.innerHTML+='<div class="settings-group"><label for="appearance-theme">Color mode</label><select id="appearance-theme"><option value="light">Light</option><option value="dark">Dark</option></select><p class="hint">Your choice is saved automatically on this browser.</p></div>';
  const cameraGrid=document.createElement('div');cameraGrid.className='camera-settings-grid';
  const cameraPreview=document.createElement('div');cameraPreview.id='camera-preview-slot';
  cameraGrid.append(camera,cameraPreview);panels.camera.append(cameraGrid);
  const locationGrid=document.createElement('div');locationGrid.className='settings-location-grid';locationGrid.append(location,mapCard);panels.location.append(locationGrid);
  const points=config.railway_calibration_draft?.length?config.railway_calibration_draft:(config.railway_calibration?.points||[]);
  const draft=!!config.railway_calibration_draft?.length;
  const summary=document.createElement('div');summary.className='settings-calibration-summary';
  summary.innerHTML=`<h3>${points.length} saved point${points.length===1?'':'s'}</h3><p>${draft?'Draft points saved on this device. Open the point matcher to apply them.':config.railway_calibration?'Your matched points are saved. Review them here or update them after moving the camera.':'Add at least two matched points to establish the rail axis.'}</p>${points.length?`<dl>${points.map((point,index)=>`<div><dt>Point ${'ABC'[index]}</dt><dd>${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}</dd></div>`).join('')}</dl>`:''}`;
  summary.append(match);
  const speedChoice=document.createElement('label');speedChoice.className='approximate-speed-choice';
  speedChoice.innerHTML=`<input id="approximate-speed" type="checkbox" ${config.approximate_speed_enabled?'checked':''}> Estimate speed from rail landmarks (approximate)<small>Two landmarks give an approximate scale. Perspective can distort speed; this is not a surveyed measurement. Applies to new recordings.</small>`;
  summary.append(speedChoice);panels.railway.append(summary);
  const sensitivity=document.createElement('div');sensitivity.className='settings-group';sensitivity.innerHTML='<h3>Detector sensitivity</h3>';sensitivity.append(confidence);panels.detection.append(sensitivity);
  const detectionPreview=document.createElement('div');detectionPreview.id='detection-preview-slot';panels.detection.append(detectionPreview,landmarks);
  panels.recording.append(recording);panels.account.append(account);
  const modelContent=document.createElement('div');modelContent.id='model-content';panels.models.append(modelContent);
  renderModels();
  const shell=document.createElement('div');shell.className='settings-shell';
  const sidebar=document.createElement('aside');sidebar.className='settings-sidebar';
  sidebar.innerHTML='<nav aria-label="Settings sections"><div role="tablist" aria-label="Settings" aria-orientation="vertical">'+[['Preferences',[['appearance','Appearance']]],['Device',[['camera','Camera'],['location','Location & view'],['railway','Railway calibration']]],['Capture',[['detection','Train detection'],['models','Models & local analysis'],['recording','Recording & storage']]],['Cloud',[['account','TrainsFYI account']]]].map(([group,items])=>`<div class="settings-nav-group"><span>${group}</span>${items.map(([id,label])=>`<button type="button" role="tab" id="settings-tab-${id}" data-settings-section="${id}" aria-controls="settings-panel-${id}">${label}</button>`).join('')}</div>`).join('')+'</div></nav>';
  const content=document.createElement('div');content.className='settings-content';Object.values(panels).forEach(item=>content.append(item));
  cameraPreview.append(preview);shell.append(sidebar,content);old.replaceWith(shell);
  $('#appearance-theme').value=document.documentElement.dataset.theme==='dark'?'dark':'light';
  $('#appearance-theme').onchange=event=>applyTheme(event.target.value);
  form.querySelector('.actions').classList.add('settings-save-actions');
  $('#error').classList.add('settings-save-error');
  form.querySelectorAll('[data-settings-section]').forEach(button=>{
    button.onclick=()=>showSettingsSection(button.dataset.settingsSection);
    button.onkeydown=event=>{const tabs=[...form.querySelectorAll('[data-settings-section]')];const index=tabs.indexOf(button);let next;if(event.key==='ArrowDown'||event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowUp'||event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();showSettingsSection(tabs[next].dataset.settingsSection);tabs[next].focus();};
  });
  showSettingsSection(settingsSection);
}
let modelPoll;
async function renderModels() {
  clearTimeout(modelPoll);
  if(!$('#model-content'))return;
  try {
    const data=await api('models');if(!$('#model-content'))return;
    $('#model-content').innerHTML=`<div class="model-source"><p>Download the latest compatible weights from the Trains.fyi model source.</p><button id="check-models" type="button" class="secondary" ${data.busy?'disabled':''}>${data.busy==='checking'?'Checking…':'Check for updates'}</button></div>${data.error?`<p class="error" role="status">${escapeHTML(data.error)}</p>`:''}${data.models.map(model=>`<section class="model-row"><div><h3>${escapeHTML(model.name)}</h3><p>Installed: <strong>${escapeHTML(model.version || 'Not installed')}</strong></p>${model.sha256?`<p class="hint">SHA-256: ${escapeHTML(model.sha256.slice(0,16))}…</p>`:''}<p>${model.latest_version?'Latest: '+escapeHTML(model.latest_version):'Check for updates to see the latest release.'}</p>${model.scope?`<p class="hint">${escapeHTML(model.scope)}</p>`:''}${model.compatible===false?'<p class="hint">No compatible release for this camera source.</p>':''}</div><button type="button" class="secondary model-download" data-model="${model.id}" ${data.busy||model.compatible!==true?'disabled':''}>${data.busy===model.id?'Downloading…':'Download latest weights'}</button></section>`).join('')}<p class="hint">Downloads are checksum-checked before activation. Detector updates wait until the current train recording ends.</p>${data.algorithms.map(algorithm=>`<section class="model-row"><div><h3>${escapeHTML(algorithm.name)}</h3><p>Version: ${escapeHTML(algorithm.version)}</p></div><span class="hint">${escapeHTML(algorithm.weights)} · no weights needed</span></section>`).join('')}`;
    $('#check-models').onclick=async()=>{try{await api('models/check','POST');renderModels();}catch(e){toast(e.message);}};
    document.querySelectorAll('.model-download').forEach(button=>button.onclick=async()=>{try{await api(`models/${button.dataset.model}/download`,'POST');renderModels();}catch(e){toast(e.message);}});
    if(data.busy)modelPoll=setTimeout(renderModels,1000);
  }catch(e){if($('#model-content'))$('#model-content').textContent=e.message;}
}
function showSettingsSection(section) {
  if(!document.querySelector(`[data-settings-panel="${section}"]`))section='camera';
  settingsSection=section;
  if(section==='models')renderModels();
  document.querySelectorAll('[data-settings-panel]').forEach(panel=>panel.hidden=panel.dataset.settingsPanel!==section);
  document.querySelectorAll('[data-settings-section]').forEach(button=>{const selected=button.dataset.settingsSection===section;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;});
  const preview=$('#settings-form .preview-card');
  if(preview&&(section==='camera'||section==='detection')){
    $('#'+section+'-preview-slot').append(preview);
    preview.querySelector('.mask-editor').hidden=section!=='detection';
    preview.querySelector('canvas').style.pointerEvents=section==='detection'?'auto':'none';
  }
  requestAnimationFrame(()=>{if(page!=='settings')return;if(map)map.invalidateSize();drawMask();scrollSettingsTabIntoView();});
}

function scrollSettingsTabIntoView() {
  if(page!=='settings')return;
  const list=$('.settings-sidebar [role="tablist"]'), selected=$('.settings-sidebar [aria-selected="true"]');
  if(!list||!selected||list.scrollWidth<=list.clientWidth)return;
  const outer=list.getBoundingClientRect(), inner=selected.getBoundingClientRect();
  list.scrollLeft+=inner.left-outer.left-(outer.width-inner.width)/2;
}
async function settingsSourceField() {
  const isRtsp=sourceType==='rtsp';
  $('#settings-source-field').innerHTML=isRtsp?`<label for="settings-source">RTSP stream URL</label><input id="settings-source" type="text" autocomplete="off" spellcheck="false" placeholder="${config.source_type==='rtsp'?'Saved stream. Leave blank to keep it.':'rtsp://camera/stream'}"><p class="hint">${config.source_type==='rtsp'?`Current stream: ${escapeHTML(config.source_label)}. Enter a new address to replace it.`:'Stream credentials are stored on this device.'}</p>`:`<label for="settings-source">Camera device</label><input id="settings-source" list="settings-devices" value="${sourceType===config.source_type&&config.source_type==='usb'?escapeHTML(config.source):'/dev/video0'}"><datalist id="settings-devices"></datalist><p class="hint" id="settings-device-hint">Checking for connected cameras…</p>`;
  if(!isRtsp)try{const devices=await api('devices');if(!$('#settings-devices'))return;$('#settings-devices').innerHTML=devices.map(d=>`<option value="${escapeHTML(d.path)}">${escapeHTML(d.label)}</option>`).join('');$('#settings-device-hint').textContent=devices.length?`${devices.length} video device(s) found.`:'No USB cameras detected. Enter a /dev/video path.';}catch(e){error(e.message);}
}
let visibleSightings = [];
const reviewLabel = row => ({required:'Needs review',confirmed:'Confirmed',dismissed:'Not a train',not_required:'—'}[row.review_status] || '—');
const eventDuration = row => row.ended ? `${Math.max(0,Math.round(row.ended-row.started))}s` : 'Recording…';
function detectionDate(stamp, now = new Date()) {
  const date = new Date(stamp * 1000);
  const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1);
  const day = sameDay(date, now) ? 'Today' : sameDay(date, yesterday) ? 'Yesterday' : date.toLocaleDateString('en-US', { weekday:'long', month:'long', day:'numeric', year:'numeric' });
  return `${day} at ${date.toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit', hour12:true })}`;
}
function detectionType(row) {
  if(row.review_status==='dismissed')return 'False Positive';
  if(row.review_status==='required')return 'Waiting For Review';
  const type=String(row.train_type||'').toLowerCase();
  return type==='passenger'?'Passenger':type==='freight'?'Freight':'Waiting For Review';
}
function eventTable(rows) {
  return `<div class="event-table-wrap"><table class="event-table"><thead><tr><th scope="col">Detected</th><th scope="col">Duration</th><th scope="col">Type</th><th scope="col">Speed</th><th scope="col">Direction</th><th scope="col">Confidence</th><th scope="col">Review</th><th scope="col">Upload</th><th scope="col"><span class="sr-only">Details</span></th></tr></thead><tbody>${rows.map(row=>`<tr data-event-id="${escapeHTML(row.id)}"><td><button type="button" class="event-open" data-event-id="${escapeHTML(row.id)}">${escapeHTML(detectionDate(row.started))}</button></td><td>${eventDuration(row)}</td><td>${escapeHTML(detectionType(row))}</td><td>${row.local_analysis?.speed?.value != null?'~'+escapeHTML(row.local_analysis.speed.value)+' km/h':'—'}</td><td>${escapeHTML(row.local_analysis?.direction?.value || '—')}</td><td>${row.manual_review?'Manually marked':`${Math.round(row.confidence*100)}%`}</td><td>${row.review_status==='not_required'?'<span class="muted">—</span>':`<span class="review-chip ${escapeHTML(row.review_status)}">${reviewLabel(row)}</span>`}</td><td><span class="badge ${escapeHTML(row.status)}">${row.status==='pending'?'Queued':escapeHTML(row.status)}</span></td><td aria-hidden="true">↗</td></tr>`).join('')}</tbody></table></div>`;
}
async function refreshSightings() {
  const rows=await api(`sightings?offset=${offset}`);if(page!=='dashboard')return;
  visibleSightings=rows;
  $('#previous').hidden=offset===0;$('#more').hidden=rows.length<50;
  const shown=dashboardView==='overview'?rows.slice(0,5):rows;
  $('#sightings').innerHTML=shown.length?eventTable(shown):`<div class="empty-state"><strong>No sightings recorded</strong><p>Detected train events and their video clips will appear here.</p></div>`;
  $('#event-count').textContent=dashboardView==='overview'?shown.length:rows.length;
  document.querySelectorAll('tr[data-event-id]').forEach(row=>row.onclick=()=>openDetection(row.dataset.eventId));
}
let stopDetectionUpdates=()=>{};
function openDetection(id) {
  stopDetectionUpdates();
  const row=visibleSightings.find(item=>item.id===id);if(!row)return;
  const dialog=$('#video-dialog'), video=dialog.querySelector('video');
  dialog.querySelector('h2').textContent='Detection details';
  let detail=$('#detection-detail');
  if(!detail){detail=document.createElement('div');detail.id='detection-detail';dialog.querySelector('.dialog-head').after(detail);}
  detail.innerHTML=`<p class="detail-time">${escapeHTML(detectionDate(row.started))}</p><dl class="detection-facts"><div><dt>Duration</dt><dd>${eventDuration(row)}</dd></div><div><dt>Confidence</dt><dd>${row.manual_review?'Manually marked':`${Math.round(row.confidence*100)}%`}</dd></div><div><dt>Type</dt><dd>${escapeHTML(row.train_type || 'Unknown')}${row.user_train_type?' · confirmed by you':''}</dd></div><div><dt>Local speed</dt><dd>${row.local_analysis?.speed?.value != null?'~'+escapeHTML(row.local_analysis.speed.value)+' km/h':'Unknown'}</dd></div><div><dt>Local direction</dt><dd>${escapeHTML(row.local_analysis?.direction?.value || 'Unknown')}</dd></div><div><dt>Upload</dt><dd>${row.status==='pending'?'Queued':escapeHTML(row.status)}</dd></div></dl><div id="review-panel"></div>${row.error?`<p class="error">${escapeHTML(row.error)}</p>`:''}${!row.video_url?'<p class="clip-unavailable">This recording is no longer available on this device.</p>':''}<div id="detail-feedback" role="status"></div>`;
  video.hidden=!row.video_url;
  if(row.video_url){mountDetectionPlayer(row, video);}else{disposeDetectionPlayer();$('#detection-player')?.remove();dialog.append(video);video.pause();video.removeAttribute('src');video.load();}
  const renderReview=(editing=false,thanks=false)=>{
    const panel=$('#review-panel');
    const reviewed=['confirmed','dismissed'].includes(row.review_status);
    const showBox=editing||row.review_status==='required';
    panel.innerHTML=showBox?`<section class="review-notice"><strong>${editing?'Update your review':'Needs review'}</strong><p>${editing?'Change whether this recording shows a train.':'Confidence is below 80%. Watch the clip and confirm whether this is a train.'}</p>${row.video_url?`<label for="review-train-type">Train type</label><select id="review-train-type"><option value="unknown">Not sure</option><option value="freight">Freight</option><option value="passenger">Passenger</option></select><div class="review-buttons"><button id="confirm-detection" type="button">Confirm train</button><button id="dismiss-detection" type="button" class="secondary">Not a train</button>${editing?'<button id="cancel-review" type="button" class="text-button">Cancel</button>':''}</div>`:'<p>The recording is no longer available to review.</p>'}<p id="review-error" role="status"></p></section>`:`<div class="review-result"><span role="status">${thanks?'Thanks — ':reviewed?'Reviewed · ':''}${reviewed?(row.review_status==='confirmed'?(row.user_train_type?'Marked as '+row.user_train_type+' train':'Marked as a train'):'Marked as not a train'):'No review needed'}</span></div>`;
    const review=async decision=>{
      const trainType=decision==='confirmed'?$('#review-train-type').value:null;
      panel.querySelectorAll('button').forEach(button=>button.disabled=true);
      try{
        await api(`sightings/${encodeURIComponent(id)}/review`,'POST',{decision,train_type:trainType});
        row.review_status=decision;row.user_train_type=trainType==='unknown'?null:trainType;row.train_type=row.user_train_type;
        if(panel.isConnected){renderReview(false,true);await refreshSightings();}
      }catch(e){if(panel.isConnected){$('#review-error').textContent=e.message;panel.querySelectorAll('button').forEach(button=>button.disabled=false);}}
    };
    if($('#review-train-type'))$('#review-train-type').value=row.user_train_type||'unknown';
    if($('#confirm-detection'))$('#confirm-detection').onclick=()=>review('confirmed');
    if($('#dismiss-detection'))$('#dismiss-detection').onclick=()=>review('dismissed');
    if($('#edit-review'))$('#edit-review').onclick=()=>renderReview(true);
    if($('#cancel-review'))$('#cancel-review').onclick=()=>renderReview();
  };
  renderReview();
  const detectionOffset=offset;
  let updateTimer,disposed=false;
  stopDetectionUpdates=()=>{disposed=true;clearTimeout(updateTimer);};
  const updateAnalysis=async()=>{
    try{
      const rows=await api(`sightings?offset=${detectionOffset}`);
      if(disposed)return;
      const latest=rows.find(item=>item.id===id);
      if(!latest)return;
      Object.assign(row,latest);
      const facts=detail.querySelectorAll('.detection-facts dd');
      facts[2].textContent=(row.train_type||'Unknown')+(row.user_train_type?' · confirmed by you':'');
      facts[3].textContent=row.local_analysis?.speed?.value!=null?'~'+row.local_analysis.speed.value+' km/h':'Unknown';
      facts[4].textContent=row.local_analysis?.direction?.value||'Unknown';
      const state=row.local_analysis_state;
      $('#detail-feedback').textContent=({queued:'Local models queued…',running:'Running local models…',completed:'Local analysis updated.',failed:'Local analysis could not finish.',cleared:'Local analysis reset.'})[state]||'';
      if(['queued','running'].includes(state))updateTimer=setTimeout(updateAnalysis,2000);
    }catch{if(!disposed)updateTimer=setTimeout(updateAnalysis,5000);}
  };
  if(['queued','running'].includes(row.local_analysis_state))updateTimer=setTimeout(updateAnalysis,1000);
  const menu=dialog.querySelector('.review-menu');menu.open=false;
  $('#edit-review').onclick=()=>{menu.open=false;renderReview(true);};
  dialog.querySelectorAll('[data-detection-action]').forEach(button=>{
    button.disabled=button.dataset.detectionAction==='analysis/rerun'&&(!row.video_url||row.status==='recording');
    button.onclick=async()=>{
      menu.open=false;
      const feedback=$('#detail-feedback');
      try{
        await api(`sightings/${encodeURIComponent(id)}/${button.dataset.detectionAction}`,'POST');
        if(!feedback.isConnected)return;
        if(button.dataset.detectionAction==='review/reset'){
          row.review_status='required';row.user_train_type=null;renderReview();
          feedback.textContent='Review reset.';
        }else feedback.textContent=button.dataset.detectionAction==='analysis/rerun'?'Local models queued. Results will update when processing finishes.':'Local analysis reset.';
        await refreshSightings();
        if(button.dataset.detectionAction.startsWith('analysis/')&&!disposed)updateTimer=setTimeout(updateAnalysis,500);
      }catch(e){if(feedback.isConnected)feedback.textContent=e.message;}
    };
  });
  if(!dialog.open)dialog.showModal();
}
let disposeDetectionPlayer = () => {};
const playerTime = seconds => { const value=Math.max(0,Math.floor(seconds||0));return `${Math.floor(value/60)}:${String(value%60).padStart(2,'0')}`; };
function mountDetectionPlayer(row, video) {
  disposeDetectionPlayer();
  $('#detection-player')?.remove();
  const player=document.createElement('section');player.id='detection-player';
  player.innerHTML=`<div class="recording-screen"></div><div class="recording-controls"><div id="player-note" class="player-note" role="status">Loading recording and detection timing…</div><div class="recording-timeline"><div class="detection-windows" aria-hidden="true"></div><input id="recording-seek" type="range" min="0" max="1" step="0.01" value="0" aria-label="Seek recording" disabled></div><div class="recording-toolbar"><button id="recording-play" class="secondary" type="button" disabled>Play</button><button id="recording-back" class="text-button" type="button" disabled aria-label="Back five seconds">−5s</button><button id="recording-forward" class="text-button" type="button" disabled aria-label="Forward five seconds">+5s</button><span id="recording-clock">0:00 / 0:00</span><button id="recording-detection" class="secondary" type="button" disabled>Play detection</button><label class="sr-only" for="recording-speed">Playback speed</label><select id="recording-speed" aria-label="Playback speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="1.5">1.5×</option><option value="2">2×</option></select><button id="recording-fullscreen" class="text-button" type="button">Full screen</button></div></div>`;
  $('#detection-detail').before(player);player.querySelector('.recording-screen').append(video);
  video.controls=false;video.muted=true;video.preload='metadata';
  const seek=$('#recording-seek'),note=$('#player-note'),play=$('#recording-play'),jump=$('#recording-detection');
  let disposed=false,windows=[];
  const controller=new AbortController();
  const duration=()=>Number.isFinite(video.duration)?video.duration:0;
  const update=()=>{
    if(disposed)return;
    seek.value=video.currentTime;seek.setAttribute('aria-valuetext',`${playerTime(video.currentTime)} of ${playerTime(duration())}`);
    $('#recording-clock').textContent=`${playerTime(video.currentTime)} / ${playerTime(duration())}`;
    play.textContent=video.paused?'Play':'Pause';
    seek.style.setProperty('--played',`${duration()?video.currentTime/duration()*100:0}%`);
  };
  const playVideo=()=>video.play().catch(()=>{if(!disposed){note.textContent='Ready. Press Play to watch the recording.';update();}});
  const seekTo=time=>{video.currentTime=Math.max(0,Math.min(duration(),time));update();};
  const metadataReady=new Promise(resolve=>video.addEventListener('loadedmetadata',resolve,{once:true,signal:controller.signal}));
  const timing=api(`sightings/${encodeURIComponent(row.id)}/timeline`).catch(()=>null);
  video.addEventListener('timeupdate',update,{signal:controller.signal});
  video.addEventListener('play',update,{signal:controller.signal});video.addEventListener('pause',update,{signal:controller.signal});video.addEventListener('ended',update,{signal:controller.signal});
  video.addEventListener('error',()=>{note.textContent='Recording unavailable. It may have expired from this device.';play.disabled=true;seek.disabled=true;jump.disabled=true;},{signal:controller.signal});
  play.onclick=()=>{if(video.paused)playVideo();else video.pause();};
  seek.oninput=()=>seekTo(+seek.value);
  $('#recording-back').onclick=()=>seekTo(video.currentTime-5);$('#recording-forward').onclick=()=>seekTo(video.currentTime+5);
  $('#recording-speed').onchange=event=>video.playbackRate=+event.target.value;
  $('#recording-fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});else player.requestFullscreen().catch(()=>toast('Fullscreen is unavailable in this browser.'));};
  jump.onclick=()=>{if(windows.length){seekTo(Math.max(0,windows[0].start-3));playVideo();}};
  Promise.all([metadataReady,timing]).then(([,timeline])=>{
    if(disposed||!duration())return;
    seek.max=duration();seek.disabled=false;play.disabled=false;
    $('#recording-back').disabled=false;$('#recording-forward').disabled=false;
    // Video files use fixed-rate frames. Scale captured UTC bounds to their media duration.
    const scale=timeline?.capture_seconds>0?duration()/timeline.capture_seconds:1;
    windows=(timeline?.windows||[]).map(w=>({start:Math.max(0,Math.min(duration(),w.start*scale)),end:Math.max(0,Math.min(duration(),w.end*scale))})).filter(w=>w.end>w.start);
    player.querySelector('.detection-windows').innerHTML=windows.map(w=>`<span style="left:${w.start/duration()*100}%;width:${(w.end-w.start)/duration()*100}%"></span>`).join('');
    jump.disabled=!windows.length;
    note.textContent=windows.length?`Highlighted: estimated detection · ${playerTime(windows[0].start)}–${playerTime(windows[windows.length-1].end)} · playback starts 3 seconds before`:'Detection timing unavailable for this recording. Playing from the beginning.';
    seekTo(windows.length?Math.max(0,windows[0].start-3):0);update();playVideo();
  });
  disposeDetectionPlayer=()=>{disposed=true;controller.abort();video.pause();video.playbackRate=1;};
  video.src=row.video_url;video.load();
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
$('#close-video').onclick=()=>$('#video-dialog').close();$('#video-dialog').addEventListener('close',()=>{stopDetectionUpdates();disposeDetectionPlayer();$('#video-dialog video').pause();$('#video-dialog video').removeAttribute('src');$('#video-dialog video').load();});
boot();
refreshSystemStatus();

function bindDevicePairing() {
  const showPairing = async () => {
    const target = $('#device-pairing'); if (!target) return;
    try {
      const device = await api('device');
      target.replaceChildren();
      $('#pair-device').hidden=device.connected;
      const status = document.createElement('p'); status.textContent = device.connected ? 'Connected to TrainsFYI' : device.status; target.append(status);
      if(device.connected){
        const profile=device.account||{};
        const name=document.createElement('strong');name.textContent=profile.display_name||profile.email||'TrainsFYI account';target.append(name);
        if(profile.email&&profile.display_name){const email=document.createElement('p');email.textContent=profile.email;target.append(email);}
        const camera=document.createElement('p');camera.className='hint';camera.textContent=`Camera: ${device.camera.name}`;target.append(camera);
        if(!profile.display_name&&!profile.email){const note=document.createElement('p');note.className='hint';note.textContent='Your account profile is temporarily unavailable.';target.append(note);}
        const logout=document.createElement('button');logout.type='button';logout.className='secondary';logout.textContent='Log out';target.append(logout);
        logout.onclick=async()=>{logout.disabled=true;try{await api('device/logout','POST');await showPairing();toast('Logged out of TrainsFYI on this device.');}catch(e){logout.disabled=false;error(e.message);}};
      }
      if (device.code) {
        const qr = document.createElement('img'); qr.src = '/api/device/qr'; qr.alt = 'Scan to authorize this camera'; qr.width = 180; qr.height = 180; target.append(qr);
        const code = document.createElement('strong'); code.textContent = device.code; target.append(code);
        const link = document.createElement('a'); link.href = device.approval_url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = 'Open account authorization'; target.append(link);
      }
    } catch (e) { target.textContent = e.message; }
  };
  $('#pair-device').onclick = async () => { try { await api('device/pair', 'POST', {}); await showPairing(); } catch (e) { error(e.message); } };
  showPairing();
  const pairingTimer = setInterval(() => { if (!$('#device-pairing')) clearInterval(pairingTimer); else showPairing(); }, 5000);
}


let railPoints = [], railDraft = null;
function calibrationPage(returnTo = settingsPage, onboarding = false) {
  if(!window.L) return toast('The map could not load. Check your connection and try again.');
  cleanup(); page='calibration'; $('#app').dataset.view=''; navigation(onboarding?'setup':'settings');
  const stored=config.railway_calibration_draft?.length?config.railway_calibration_draft:(config.railway_calibration?.points || []);
  railPoints=stored.map(point=>({...point,image:[...point.image]}));
  railDraft=railPoints.length<2?{index:railPoints.length}:null;
  let busy=false, selection='image', markers=[];
  $('#app').innerHTML=`<div id="calibration-workspace">${onboarding?setupProgress(2):''}<div class="intro"><div><h1>Match two rail landmarks</h1><p>Choose two recognizable spots on the same straight rail, such as a crossing or a track joint. Mark each spot in the live image and at its matching railway location on the map. Keep A and B well apart.</p></div><button class="secondary" id="calibration-back">${onboarding?'← Back to location':'Back to settings'}</button></div>
    <section class="card rail-progress"><div id="rail-saved" aria-live="polite"></div><button class="secondary" id="rail-third">Add point C (optional)</button><button class="text-button" id="rail-reset">Camera moved / clear all points</button></section>
    <section class="card rail-guidance"><h2 id="rail-instruction"></h2><div class="rail-steps"><button class="secondary" id="rail-select-image">1. Choose camera point</button><button class="secondary" id="rail-select-map">2. Choose matching map point</button></div><p id="rail-feedback" role="status"></p></section>
    <div class="settings-grid">${previewHTML('calibration',false)}<section class="card"><h3>Matching map location</h3><div id="map" style="height:420px"></div><p>Click the same spot on the same rail. You can drag the selected map pin to adjust it.</p></section></div>
    <p>Two saved pairs establish the rail axis. A third pair can support future speed calibration. Recalibrate after moving or zooming the camera.</p>
    <div class="rail-save-bar"><div id="error" class="error-box" hidden></div><span id="rail-summary" aria-live="polite"></span><button id="rail-save-point">Save point A</button><button id="rail-save" class="secondary">Apply calibration</button></div></div>`;
  $('#calibration-back').onclick=onboarding?()=>setup(1):returnTo;
  if(onboarding) $('#rail-save').textContent='Save landmarks & continue';
  map=L.map('map').setView([config.latitude??43.65,config.longitude??-79.38],18);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{attribution:'Imagery © Esri',maxZoom:20}).addTo(map);
  L.tileLayer('https://tiles.openrailwaymap.org/standard/{z}/{x}/{y}.png',{attribution:'© OpenRailwayMap, © OpenStreetMap contributors',maxZoom:19}).addTo(map);
  const hasMap=()=>railDraft?.latitude!==undefined;
  const refresh=()=>{
    markers.forEach(item=>map.removeLayer(item));markers=[];
    const points=railPoints.map((point,index)=>({...point,index})).filter(point=>point.index!==railDraft?.index);
    if(hasMap())points.push(railDraft);
    points.forEach(point=>{
      const editing=point.index===railDraft?.index;
      const pin=L.marker([point.latitude,point.longitude],{draggable:editing&&!busy,title:`Point ${'ABC'[point.index]}`,icon:L.divIcon({className:'rail-map-marker',html:`<span class="rail-map-pin">${'ABC'[point.index]}</span>`,iconSize:[30,30],iconAnchor:[15,15]})}).addTo(map);
      if(editing)pin.on('dragend',()=>{const p=pin.getLatLng();railDraft.latitude=p.lat;railDraft.longitude=p.lng;refresh();});
      markers.push(pin);
    });
    const label=railDraft?'ABC'[railDraft.index]:null;
    $('#rail-instruction').textContent=label?`Point ${label}: ${selection==='image'?'choose a fixed spot in the camera image':'choose the same spot on the map'}`:'Points saved. Apply your calibration below.';
    $('#rail-select-image').textContent=`1. Camera point${railDraft?.image?' ✓':''}`;
    $('#rail-select-map').textContent=`2. Map point${hasMap()?' ✓':''}`;
    $('#rail-select-image').disabled=!railDraft||busy;$('#rail-select-map').disabled=!railDraft?.image||busy;
    $('#rail-select-image').setAttribute('aria-pressed',String(!!railDraft&&selection==='image'));
    $('#rail-select-map').setAttribute('aria-pressed',String(!!railDraft&&selection==='map'));
    $('#rail-feedback').textContent=railDraft?.image&&hasMap()?`Both locations selected. Click Save point ${label}.`:railDraft?.image?'Camera point selected. Now click its matching map location.':railDraft?'Click the camera image first.':'Each point is saved on this device. Apply calibration to use the matched pairs.';
    $('#rail-saved').innerHTML=railPoints.length?railPoints.map((point,i)=>`<div class="rail-saved-point"><strong>Point ${'ABC'[i]} · Saved</strong><span>${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}</span><button class="secondary rail-edit" data-index="${i}" ${busy?'disabled':''}>Edit point ${'ABC'[i]}</button></div>`).join(''):'<p>No saved points yet. Start with point A below.</p>';
    document.querySelectorAll('.rail-edit').forEach(button=>button.onclick=()=>{const index=+button.dataset.index;railDraft={...railPoints[index],image:[...railPoints[index].image],index};selection='image';refresh();$('#rail-instruction').scrollIntoView({block:'center'});});
    $('#rail-summary').textContent=`${railPoints.length} saved point${railPoints.length===1?'':'s'}${railPoints.length<2?' · save at least two to apply':''}`;
    $('#rail-save-point').textContent=label?`Save point ${label}`:'All points saved';
    $('#rail-save-point').disabled=busy||!railDraft?.image||!hasMap();
    const editing=railDraft&&(railDraft.image||hasMap());
    $('#rail-save').disabled=busy||railPoints.length<2||!!editing;
    $('#rail-save').classList.toggle('secondary',!!railDraft);
    $('#rail-save-point').classList.toggle('secondary',!railDraft);
    $('#rail-third').disabled=busy||railPoints.length!==2||!!editing;
    $('#rail-reset').disabled=busy;
    drawMask();
  };
  $('#mask-canvas').onclick=event=>{
    if(busy||!railDraft||selection!=='image')return;
    if(!$('#preview-image')?.naturalWidth || $('#preview-image').hidden) return toast('Wait for a camera picture before marking a rail landmark.');
    const rect=event.currentTarget.getBoundingClientRect();
    railDraft.image=[Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];
    selection='map';refresh();
  };
  map.on('click',event=>{if(busy||!railDraft?.image||selection!=='map')return;railDraft.latitude=event.latlng.lat;railDraft.longitude=event.latlng.lng;refresh();});
  $('#rail-select-image').onclick=()=>{selection='image';refresh();};
  $('#rail-select-map').onclick=()=>{selection='map';refresh();};
  $('#rail-third').onclick=()=>{railDraft={index:2};selection='image';refresh();};
  $('#rail-save-point').onclick=async()=>{
    if(!railDraft?.image||!hasMap()||busy)return;
    busy=true;refresh();$('#error').hidden=true;
    try{
      const index=railDraft.index;
      const next=railPoints.map(point=>({...point,image:[...point.image]}));
      next[index]={image:[...railDraft.image],latitude:railDraft.latitude,longitude:railDraft.longitude};
      config=await api('config','PATCH',{railway_calibration_draft:next});
      railPoints=config.railway_calibration_draft.map(point=>({...point,image:[...point.image]}));
      railDraft=railPoints.length<2?{index:railPoints.length}:null;selection='image';
      toast(`Point ${'ABC'[index]} saved on this device.`);
    }catch(e){error(e.message);}finally{busy=false;refresh();}
  };
  $('#rail-reset').onclick=async()=>{if(busy)return;busy=true;refresh();try{config=await api('config','PATCH',{railway_calibration:null,railway_calibration_draft:[]});railPoints=[];railDraft={index:0};selection='image';}catch(e){error(e.message);}finally{busy=false;refresh();}};
  $('#rail-save').onclick=async()=>{if(busy)return;busy=true;refresh();try{config=await api('config','PATCH',{railway_calibration:{revision:'new',points:railPoints},railway_calibration_draft:[]});toast('Calibration saved. Verification is needed before estimating speed.');returnTo();}catch(e){error(e.message);}finally{busy=false;if(page==='calibration')refresh();}};
  startPreview();refresh();window.onresize=drawMask;
}


let softwareStatus = null, softwareInstalling = false;
function renderSoftware() {
  const state=softwareStatus;
  if(!state)return;
  const operation=state.operation || {}, busy=['queued','installing','restarting'].includes(operation.status);
  const notice=$('#software-update-notice');
  notice.hidden=!state.update_available&&!busy&&operation.status!=='failed';
  notice.textContent=busy?'Updating…':operation.status==='failed'?'Update needs attention':'Please update';
  const target=$('#software-content');
  target.innerHTML=`<p>Installed version: <strong>${escapeHTML(state.current_version)}</strong></p>${state.update_available?`<p><strong>${escapeHTML(state.latest_version)}</strong> is available${state.prerelease?' (prerelease)':''}.</p>`:'<p>You have the latest available release.</p>'}${operation.message?`<p role="status">${escapeHTML(operation.message)}</p>`:''}${busy?'<p role="status">Updating RailWatch. This page will reconnect automatically.</p>':state.update_available&&state.one_click?'<p>Your settings, recordings and account connection will be kept. Monitoring pauses briefly when RailWatch restarts.</p><button id="install-software" type="button">Update now</button>':state.update_available?'<p>For Docker, rebuild and restart using the release instructions. Raspberry Pi installs support one-click updates after running the new installer once.</p>':''}<p><a href="${escapeHTML(state.release_url||state.releases_url)}" target="_blank" rel="noopener noreferrer">Release notes and update instructions ↗</a></p><p class="hint">Checks GitHub once a day, including pilot prereleases.${state.error?' '+escapeHTML(state.error):''}</p>`;
  const install=$('#install-software');
  if(install)install.onclick=async()=>{
    install.disabled=true;
    try{await api('software/install','POST');softwareInstalling=true;await refreshSoftware(false);}
    catch(e){toast(e.message);install.disabled=false;}
  };
  if(softwareInstalling&&!state.update_available&&operation.status==='complete'){
    softwareInstalling=false;toast('RailWatch updated successfully.');
  }
}
async function refreshSoftware(schedule=true) {
  try {softwareStatus=await api('software');renderSoftware();}
  catch {if(softwareInstalling)$('#software-content').innerHTML='<p role="status">RailWatch is restarting. Reconnecting…</p>';}
  if(schedule)setTimeout(refreshSoftware,softwareInstalling?3000:60000);
  else if(softwareInstalling)setTimeout(()=>refreshSoftware(false),3000);
}
$('#software-update-notice').onclick=()=>{renderSoftware();$('#software-dialog').showModal();};
$('#close-software').onclick=()=>$('#software-dialog').close();
refreshSoftware();
