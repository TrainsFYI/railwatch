const $ = (q) => document.querySelector(q);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let config, step = 0, page = 'setup', map, marker, wedge, previewTimer, statusTimer, offset = 0;
let sourceType = 'usb', locationChosen = false, detectionMask = [];
let landmarks = [], selectedLandmark = '', landmarkDrag = null;
const stepNames = ['Camera', 'Location', 'Account', 'Review'];
function updateThemeToggle() {
  const dark = document.documentElement.dataset.theme === 'dark';
  const button = $('#theme-toggle');
  if (!button) return;
  button.setAttribute('aria-pressed', String(dark));
  button.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} mode`);
  $('#theme-label').textContent = dark ? 'Light mode' : 'Dark mode';
  $('.theme-icon').textContent = dark ? '☀' : '☾';
  $('meta[name="theme-color"]').content = dark ? '#151517' : '#ffffff';
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
function landmarkEditorHTML() {
  return `<div class="landmark-editor"><h3>Highlightable landmarks</h3><p>Label fixed features such as crossing lights or a gate. Select a landmark, then drag a box around it. Drag again to replace its box.</p><label for="camera-edit-mode">Image editing mode</label><select id="camera-edit-mode"><option value="detection">Detection area</option><option value="landmarks">Landmark boxes</option></select><div class="landmark-controls"><div><label for="landmark-select">Selected landmark</label><select id="landmark-select"></select></div><button type="button" class="secondary" id="landmark-add">Add landmark</button></div><label for="landmark-label">Landmark label</label><input id="landmark-label" maxlength="80" placeholder="e.g. Crossing light, left"><button type="button" class="text-button" id="landmark-remove">Remove selected landmark</button><p class="hint">Save settings to keep your boxes and labels. Review footage can filter repeated red flashes within these boxes.</p></div>`;
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
    if((page==='calibration'||page==='settings') && image.naturalWidth && !document.fullscreenElement){
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
  $('#step-content').innerHTML = `<div class="setup-grid"><section class="card"><h2>Camera location</h2><button class="secondary" id="setup-rail-calibration">Match railway points (optional)</button><p>Place the marker on the map or enter coordinates. The overlay shows mapped railway lines.</p><div class="inline-inputs"><div><label for="latitude">Latitude</label><input id="latitude" type="number" step="any" min="-85" max="85" placeholder="43.6426" value="${config.latitude??''}"></div><div><label for="longitude">Longitude</label><input id="longitude" type="number" step="any" min="-180" max="180" placeholder="-79.3871" value="${config.longitude??''}"></div></div><div class="map-controls"><button class="secondary" id="go-coordinates">Show coordinates</button><button class="secondary" id="locate">Use my location</button></div><label class="bearing-label" for="bearing"><span>Camera direction</span><span id="bearing-value">${config.bearing}° · ${compass(config.bearing)}</span></label><input id="bearing" type="range" min="0" max="359" value="${config.bearing}"><p class="hint">0° is north, 90° is east. Point the cone where your camera looks.</p><label class="bearing-label" for="fov"><span>Field of view</span><span id="fov-value">${config.field_of_view}°</span></label><input id="fov" type="range" min="10" max="160" value="${config.field_of_view}"><div class="note">Satellite imagery and rail overlays need an internet connection. You can also enter coordinates manually.</div></section><section class="card"><div id="map"></div><p class="map-note" id="map-message">Click the map to place the camera. Drag to adjust.</p></section></div>`;
  mountMap(); bindLocationControls();
  $('#setup-rail-calibration').onclick=async()=>{try{await saveStep();calibrationPage(()=>setup(1));}catch(e){error(e.message);}};
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
function accountStep() {
  $('#step-content').innerHTML=`<div class="setup-grid"><section class="card" id="night-review-settings"><h2>Night review</h2><label><input type="checkbox" id="night-review-enabled" ${config.night_review_enabled?'checked':''}> Retain nighttime footage for local review</label><p class="hint">Keep extra footage outside the rolling 24-hour archive. This does not automatically upload candidates. Review footage → All footage includes retained recordings.</p><label for="night-review-timezone">Timezone</label><input id="night-review-timezone" value="${escapeHTML(config.night_review_timezone||'UTC')}" placeholder="America/Toronto"><div class="inline-inputs"><div><label for="night-start">Start hour (0–23)</label><input id="night-start" type="number" min="0" max="23" value="${config.night_review_start_hour??19}"></div><div><label for="night-end">End hour (0–23)</label><input id="night-end" type="number" min="0" max="23" value="${config.night_review_end_hour??7}"></div></div><div class="inline-inputs"><div><label for="night-hours">Retention (hours)</label><input id="night-hours" type="number" min="24" max="168" value="${config.night_review_hours??72}"></div><div><label for="night-gb">Extra storage budget (GB)</label><input id="night-gb" type="number" min="0.25" max="20" step="0.25" value="${config.night_review_gb??2}"></div></div><p class="hint">Oldest review footage expires when either limit is reached. Pending uploads are preserved independently.</p></section>
    <section class="card"><h2>TrainsFYI account</h2><p>Authorize this camera in your browser. Confirmed train passages upload automatically; footage stays private.</p><button type="button" id="pair-device">Connect to TrainsFYI</button><div id="device-pairing" aria-live="polite"></div><div hidden><input id="api-key"><input id="clear-key" type="checkbox"><input id="upload-url" value="${escapeHTML(config.upload_url)}"></div></section><section class="setup-aside"><h3>What is uploaded</h3><p>Only recorded train clips and camera location details are sent. Live video stays on this device.</p><p>If the network is unavailable, clips remain queued here for a later retry.</p></section></div>`;
  bindDevicePairing();
}
function reviewStep() {
  $('#step-content').innerHTML=`<div class="setup-grid"><section class="card"><h2>Review settings</h2>${[['Name',config.name],['Camera',config.source_label],['Position',`${config.latitude?.toFixed(6)}, ${config.longitude?.toFixed(6)}`],['Direction',`${config.bearing}° ${compass(config.bearing)} · ${config.field_of_view}° view`],['TrainsFYI','Manage account connection in Settings']].map(([a,b])=>`<div class="review-item"><span>${a}</span><strong>${escapeHTML(b)}</strong></div>`).join('')}<div class="inline-inputs"><div><label for="confidence">Detection confidence</label><input id="confidence" type="number" min="0.1" max="0.99" step="0.05" value="${config.confidence}"></div><div><label for="storage">Recording storage (GB)</label><input id="storage" type="number" min="1" max="1000" step="0.25" value="${config.storage_gb}"></div></div><p class="hint">${config.pre_roll}s before detection · ${config.post_roll}s after the last detection. Long trains are saved in consecutive clips.</p><div id="model-note" class="note">Checking the local detector…</div></section>${previewHTML()}</div>`;
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
  $('#app').innerHTML=`<div class="intro"><div><h1>Review footage</h1><p>Find model-detected trains inside your detection area and review their passages.</p></div><button class="secondary" id="back-review">Back to dashboard</button></div><div class="review-layout"><section class="card review-player"><div class="archive-view"><video id="archive-video" controls playsinline preload="metadata"></video><svg class="archive-area" viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="Tracked detection area"><polygon points="${detectionMask.map(([x,y])=>`${x*100},${y*100}`).join(' ')}"></polygon>${(config.landmarks||[]).map(item=>`<rect x="${item.box[0]*100}" y="${item.box[1]*100}" width="${(item.box[2]-item.box[0])*100}" height="${(item.box[3]-item.box[1])*100}" fill="none" stroke="#57dbff" stroke-width="0.3"><title>${escapeHTML(item.label)}</title></rect>`).join('')}</svg></div><label for="activity-filter">Look for</label><select id="activity-filter"><option value="trains">Train model detections</option><option value="red_flashes">Red flashes in landmarks (paused)</option><option value="movement">Significant movement</option></select><label for="movement-filter">Show footage</label><select id="movement-filter"><option value="motion">Significant movement in tracked area</option><option value="all">All footage</option><option value="quiet">No significant movement detected</option><option value="pending">Not analysed yet</option><option value="error">Analysis unavailable</option></select><p class="hint" id="activity-hint">Requires at least 15% of the tracked area to change for about two seconds. Candidates can include vehicles, people, foliage and shadows. They are not confirmed trains. No significant movement detected does not prove an empty track.</p><div class="review-note" id="review-note" role="status">Loading available footage…</div><div id="motion-windows"></div><div class="review-list" id="review-list"></div></section><section class="card mark-card"><h2>Mark a train</h2><p>Select the start and end of the train’s passage. Railwatcher saves it as a manual sighting for local review. Select Submit to TrainsFYI to upload it explicitly.</p><form id="mark-form" hidden><label for="mark-start">Start time</label><input id="mark-start" type="datetime-local" step="1" required><label for="mark-end">End time</label><input id="mark-end" type="datetime-local" step="1" required><button id="mark-submit" type="submit">Mark train and save clip</button></form><p class="hint">Selected footage must be within the available archive and shorter than 15 minutes.</p></section></div>`;
  $('#back-review').onclick=dashboard;
  $('#activity-filter').value='trains';
  const updateActivity = () => {
    const flashing=$('#activity-filter').value==='red_flashes', train=$('#activity-filter').value==='trains';
    $('#movement-filter').options[0].textContent=train?'Train model candidates':flashing?'Repeated red flashes':'Significant movement in tracked area';
    $('#movement-filter').options[2].textContent=train?'No train detected':flashing?'No red flashes detected':'No significant movement detected';
    $('#activity-hint').textContent=train?'Uses the same train model, detection area, confidence threshold and confirmations within five seconds as live recording. Indexing runs newest first. Missed detections remain possible.':flashing?(config.landmarks?.length?'Looks for at least three red on/off cycles within saved landmark boxes. Candidates indicate crossing activity, not confirmed trains.':'Add a landmark around a crossing lamp in Camera settings first.'):'Requires at least 15% of the tracked area to change for about two seconds. Movement candidates are not confirmed trains.';
  };
  updateActivity();
  let selectedId=null, segments=[], playbackSegments=[], playbackIds=null, requestNumber=0;
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
  const selectSegment=(id, continuing=false)=>{
    if (!continuing) playbackIds=null;
    const segment=playbackSegments.find(row=>row.id===id) || segments.find(row=>row.id===id);if(!segment)return;
    selectedId=id;
    document.querySelectorAll('.review-segment').forEach(button=>button.classList.toggle('selected',button.dataset.id===id));
    const video=$('#archive-video');video.onloadedmetadata=null;video.src=segment.video_url;video.load();
    video.onended = () => { const following = playbackSegments.find(row => row.started >= segment.ended - 2 && row.started <= segment.ended + 5 && (!playbackIds || playbackIds.includes(row.id))); if (following && following.id !== segment.id) { selectSegment(following.id, true); video.play().catch(() => {}); } };
    $('#mark-form').hidden=false;
    $('#mark-start').value=localDateTime(segment.started);$('#mark-end').value=localDateTime(segment.ended);
    const windows=segment.motion.windows;
    $('#motion-windows').innerHTML=windows.length?`<p>Jump to activity:</p>${windows.map((window,index)=>`<button type="button" class="secondary motion-jump" data-index="${index}">${window.label?escapeHTML(window.label)+' · ':''}${Math.floor(window.started/60)}:${String(Math.floor(window.started%60)).padStart(2,'0')}–${Math.floor(window.ended/60)}:${String(Math.ceil(window.ended%60)).padStart(2,'0')}</button>`).join('')}`:'';
    document.querySelectorAll('.motion-jump').forEach(button=>button.onclick=()=>selectWindow(segment,windows[+button.dataset.index]));
    if(windows.length)selectWindow(segment,windows[0]);
  };
  const refresh=async()=>{
    clearTimeout(statusTimer);
    const request=++requestNumber;
    try {
      const data=await api(`buffer?movement=${$('#movement-filter').value}&activity=${$('#activity-filter').value}`);
      if(page!=='review'||request!==requestNumber)return;
      segments=[...data.segments].reverse(); playbackSegments=[...(data.playback_segments || data.segments)].sort((a,b)=>a.started-b.started);
      const counts=data.motion_counts, total=Object.values(counts).reduce((a,b)=>a+b,0);
      const note=$('#review-note');
      const flashing=$('#activity-filter').value==='red_flashes', train=$('#activity-filter').value==='trains';
      note.textContent=total?`${counts.motion} segments with ${train?'train detections':flashing?'red flashes':'significant movement'} · ${total-counts.pending} / ${total} analysed · ${counts.pending} waiting · ${counts.error} unavailable · ${(data.available_seconds/3600).toFixed(1)} hours retained`:(data.active?'The camera is building its first footage segment. Check back in a couple of minutes.':'No archived footage is available yet. Start camera monitoring to build the rolling archive.');
      const labels={motion:train?'Train detected':flashing?'Red flashes':'Significant movement',quiet:train?'No train detected':flashing?'No red flashes detected':'No significant movement detected',pending:'Not analysed',error:'Analysis unavailable'};
      $('#review-list').innerHTML=segments.length?`<h2>Available times</h2>${segments.map(segment=>`<button type="button" class="review-segment ${segment.id===selectedId?'selected':''}" data-id="${escapeHTML(segment.id)}"><span>${new Date(segment.started*1000).toLocaleString(undefined,{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</span><small>${segment.retained_for_review?'Night review · ':''}${labels[segment.motion.state]} · ${Math.max(1,Math.round((segment.ended-segment.started)/60))} min</small></button>`).join('')}`:total?'<p>No footage matches this filter yet. Analysis continues in the background; try All footage or Not analysed yet.</p>':'';
      if (train && data.archive_passages?.length) {
        const groups = document.createElement('div');
        data.archive_passages.forEach(group => { const button = document.createElement('button'); button.type = 'button'; button.className = 'secondary'; button.textContent = `Passage ${new Date(group.detected_started*1000).toLocaleTimeString()} · ${group.segments.length} files`; button.onclick = () => { selectSegment(group.segments[0]); playbackIds=group.segments; $('#mark-start').value = localDateTime(group.detected_started-12); $('#mark-end').value = localDateTime(group.detected_ended+15); }; groups.append(button); });
        $('#review-list').prepend(groups);
      }
      document.querySelectorAll('.review-segment').forEach(button=>button.onclick=()=>selectSegment(button.dataset.id));
      if(!segments.some(segment=>segment.id===selectedId) && !(playbackIds?.includes(selectedId) && playbackSegments.some(segment=>segment.id===selectedId))){
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
  $('#activity-filter').onchange=()=>{selectedId=null;updateActivity();refresh();};
  $('#mark-form').onsubmit=async event=>{
    event.preventDefault();const submit=$('#mark-submit');submit.disabled=true;
    try {
      const result=await api('buffer/mark','POST',{started:Date.parse($('#mark-start').value)/1000,ended:Date.parse($('#mark-end').value)/1000});
      toast('Manual train sighting saved to historical events.');
      const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Submit this historical event to TrainsFYI';
      button.onclick = async () => { try { await api(`sightings/${result.id}/submit`, 'POST', {}); button.disabled = true; button.textContent = 'Historical event queued for private upload'; } catch (e) { toast(e.message); } };
      $('#mark-form').append(button);
      if(result.video_url){const video=$('#archive-video');video.onloadedmetadata=null;video.src=result.video_url;video.load();}
    }catch(e){toast(e.message);}
    finally{if(submit.isConnected)submit.disabled=false;}
  };
  refresh();
}
let settingsSection = 'camera';
function settingsPage(section = settingsSection) {
  settingsSection=typeof section==='string'?section:settingsSection;
  cleanup(); page='settings'; locationChosen=config.latitude!==null&&config.longitude!==null; sourceType=config.source_type;
  detectionMask = (config.detection_mask || []).map(point => [...point]);
  landmarks = (config.landmarks || []).map(item=>({...item,box:[...item.box]}));
  selectedLandmark=landmarks[0]?.id || ''; landmarkDrag=null;
  $('#app').innerHTML=`<div class="intro"><div><h1>Settings</h1><p>Camera, location, detection and upload configuration.</p></div><button class="secondary" id="back-dashboard">Back to dashboard</button></div><form id="settings-form"><div class="settings-grid">
    <section class="card"><h2>Camera</h2><label for="settings-name">Camera name</label><input id="settings-name" maxlength="80" required value="${escapeHTML(config.name)}"><label for="settings-source-type">Video source</label><select id="settings-source-type"><option value="usb" ${sourceType==='usb'?'selected':''}>USB camera</option><option value="rtsp" ${sourceType==='rtsp'?'selected':''}>RTSP network stream</option></select><div id="settings-source-field"></div></section>
    <section class="card"><h2>Location and direction</h2><button type="button" class="secondary" id="rail-calibration">Match railway points</button><p>Place the camera on the map. Railway lines are shown over satellite imagery.</p><div class="inline-inputs"><div><label for="latitude">Latitude</label><input id="latitude" type="number" step="any" min="-85" max="85" value="${config.latitude??''}" required></div><div><label for="longitude">Longitude</label><input id="longitude" type="number" step="any" min="-180" max="180" value="${config.longitude??''}" required></div></div><div class="map-controls"><button class="secondary" type="button" id="go-coordinates">Show coordinates</button><button class="secondary" type="button" id="locate">Use my location</button></div><label class="bearing-label" for="bearing"><span>Camera direction</span><span id="bearing-value">${config.bearing}° · ${compass(config.bearing)}</span></label><input id="bearing" type="range" min="0" max="359" value="${config.bearing}"><label class="bearing-label" for="fov"><span>Field of view</span><span id="fov-value">${config.field_of_view}°</span></label><input id="fov" type="range" min="10" max="160" value="${config.field_of_view}"></section>
    <section class="card settings-map"><div id="map"></div><p class="map-note" id="map-message">Click the map to place the camera. Drag to adjust.</p></section>
    <section class="card"><h2>Detection and storage</h2><div class="inline-inputs"><div><label for="confidence">Detection confidence</label><input id="confidence" type="number" min="0.1" max="0.99" step="0.05" value="${config.confidence}"></div><div><label for="confirmations">Confirmations within 5 seconds</label><input id="confirmations" type="number" min="1" max="10" step="1" value="${config.confirmations}"></div></div><div class="inline-inputs"><div><label for="pre-roll">Pre-roll (seconds)</label><input id="pre-roll" type="number" min="3" max="120" value="${config.pre_roll}"></div><div><label for="post-roll">Post-roll (seconds)</label><input id="post-roll" type="number" min="3" max="60" value="${config.post_roll}"></div></div><div class="inline-inputs"><div><label for="max-clip">Maximum clip (seconds)</label><input id="max-clip" type="number" min="30" max="900" value="${config.max_clip_seconds}"></div><div><label for="storage">Storage limit (GB)</label><input id="storage" type="number" min="1" max="1000" step="0.25" value="${config.storage_gb}"></div></div></section>
    <section class="card" id="night-review-settings"><h2>Night review</h2><label><input type="checkbox" id="night-review-enabled" ${config.night_review_enabled?'checked':''}> Retain nighttime footage for local review</label><p class="hint">Keep extra footage outside the rolling 24-hour archive. This does not automatically upload candidates. Review footage → All footage includes retained recordings.</p><label for="night-review-timezone">Timezone</label><input id="night-review-timezone" value="${escapeHTML(config.night_review_timezone||'UTC')}" placeholder="America/Toronto"><div class="inline-inputs"><div><label for="night-start">Start hour (0–23)</label><input id="night-start" type="number" min="0" max="23" value="${config.night_review_start_hour??19}"></div><div><label for="night-end">End hour (0–23)</label><input id="night-end" type="number" min="0" max="23" value="${config.night_review_end_hour??7}"></div></div><div class="inline-inputs"><div><label for="night-hours">Retention (hours)</label><input id="night-hours" type="number" min="24" max="168" value="${config.night_review_hours??72}"></div><div><label for="night-gb">Extra storage budget (GB)</label><input id="night-gb" type="number" min="0.25" max="20" step="0.25" value="${config.night_review_gb??2}"></div></div><p class="hint">Oldest review footage expires when either limit is reached. Pending uploads are preserved independently.</p></section>
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
  $('#settings-form').onsubmit=async event=>{event.preventDefault();const form=event.currentTarget;if(!form.checkValidity()){const invalid=form.querySelector(':invalid');showSettingsSection(invalid.closest('[data-settings-panel]').dataset.settingsPanel);invalid.reportValidity();return;}const button=$('#save-settings');button.disabled=true;$('#error').hidden=true;try{const [latitude,longitude]=coordinates();const source=$('#settings-source').value.trim();if(!source&&sourceType!==config.source_type)throw new Error('Enter a camera address or device.');if(detectionMask.length>0&&detectionMask.length<3)throw new Error('Add at least three points for a detection area, or clear it to use the full image.');config=await api('config','PATCH',{name:$('#settings-name').value.trim(),source_type:sourceType,source:source||null,detection_mask:detectionMask,landmarks,latitude,longitude,bearing:+$('#bearing').value,field_of_view:+$('#fov').value,confidence:+$('#confidence').value,confirmations:+$('#confirmations').value,pre_roll:+$('#pre-roll').value,post_roll:+$('#post-roll').value,max_clip_seconds:+$('#max-clip').value,storage_gb:+$('#storage').value,night_review_enabled:$('#night-review-enabled').checked,night_review_timezone:$('#night-review-timezone').value.trim(),night_review_start_hour:+$('#night-start').value,night_review_end_hour:+$('#night-end').value,night_review_hours:+$('#night-hours').value,night_review_gb:+$('#night-gb').value,api_key:$('#clear-key').checked?'':($('#api-key').value.trim()||null),upload_url:$('#upload-url').value.trim()});toast('Settings saved.');settingsPage();}catch(e){error(e.message);}finally{if(button.isConnected)button.disabled=false;}};
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
    camera:panel('camera','Camera','Name your camera and manage its video connection.'),
    location:panel('location','Location & view','Set where the camera is installed and the view it covers.'),
    railway:panel('railway','Railway calibration','Match fixed points in the camera image with the same points on the map.'),
    detection:panel('detection','Train detection','Choose the track area, detector sensitivity, and labeled landmarks.'),
    recording:panel('recording','Recording & storage','Control clip padding, recording length, and local storage.'),
    account:panel('account','TrainsFYI account','Connect this device to your account for private uploads.')
  };
  const cameraGrid=document.createElement('div');cameraGrid.className='camera-settings-grid';
  const cameraPreview=document.createElement('div');cameraPreview.id='camera-preview-slot';
  cameraGrid.append(camera,cameraPreview);panels.camera.append(cameraGrid);
  const locationGrid=document.createElement('div');locationGrid.className='settings-location-grid';locationGrid.append(location,mapCard);panels.location.append(locationGrid);
  const points=config.railway_calibration_draft?.length?config.railway_calibration_draft:(config.railway_calibration?.points||[]);
  const draft=!!config.railway_calibration_draft?.length;
  const summary=document.createElement('div');summary.className='settings-calibration-summary';
  summary.innerHTML=`<h3>${points.length} saved point${points.length===1?'':'s'}</h3><p>${draft?'Draft points saved on this device. Open the point matcher to apply them.':config.railway_calibration?'Your matched points are saved. Review them here or update them after moving the camera.':'Add at least two matched points to establish the rail axis.'}</p>${points.length?`<dl>${points.map((point,index)=>`<div><dt>Point ${'ABC'[index]}</dt><dd>${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}</dd></div>`).join('')}</dl>`:''}`;
  summary.append(match);panels.railway.append(summary);
  const sensitivity=document.createElement('div');sensitivity.className='settings-group';sensitivity.innerHTML='<h3>Detector sensitivity</h3>';sensitivity.append(confidence);panels.detection.append(sensitivity);
  const detectionPreview=document.createElement('div');detectionPreview.id='detection-preview-slot';panels.detection.append(detectionPreview,landmarks);
  panels.recording.append(recording);panels.account.append(account);
  const shell=document.createElement('div');shell.className='settings-shell';
  const sidebar=document.createElement('aside');sidebar.className='settings-sidebar';
  sidebar.innerHTML='<nav aria-label="Settings sections"><div role="tablist" aria-label="Settings" aria-orientation="vertical">'+[['Device',[['camera','Camera'],['location','Location & view'],['railway','Railway calibration']]],['Capture',[['detection','Train detection'],['recording','Recording & storage']]],['Cloud',[['account','TrainsFYI account']]]].map(([group,items])=>`<div class="settings-nav-group"><span>${group}</span>${items.map(([id,label])=>`<button type="button" role="tab" id="settings-tab-${id}" data-settings-section="${id}" aria-controls="settings-panel-${id}">${label}</button>`).join('')}</div>`).join('')+'</div></nav>';
  const content=document.createElement('div');content.className='settings-content';Object.values(panels).forEach(item=>content.append(item));
  cameraPreview.append(preview);shell.append(sidebar,content);old.replaceWith(shell);
  form.querySelector('.actions').classList.add('settings-save-actions');
  $('#error').classList.add('settings-save-error');
  form.querySelectorAll('[data-settings-section]').forEach(button=>{
    button.onclick=()=>showSettingsSection(button.dataset.settingsSection);
    button.onkeydown=event=>{const tabs=[...form.querySelectorAll('[data-settings-section]')];const index=tabs.indexOf(button);let next;if(event.key==='ArrowDown'||event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowUp'||event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;event.preventDefault();showSettingsSection(tabs[next].dataset.settingsSection);tabs[next].focus();};
  });
  showSettingsSection(settingsSection);
}
function showSettingsSection(section) {
  if(!document.querySelector(`[data-settings-panel="${section}"]`))section='camera';
  settingsSection=section;
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

function bindDevicePairing() {
  const showPairing = async () => {
    const target = $('#device-pairing'); if (!target) return;
    try {
      const device = await api('device');
      target.replaceChildren();
      const status = document.createElement('p'); status.textContent = device.connected ? `Connected: ${device.camera.name}` : device.status; target.append(status);
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
function calibrationPage(returnTo = settingsPage) {
  if(!window.L) return toast('The map could not load. Check your connection and try again.');
  cleanup(); page='calibration';
  const stored=config.railway_calibration_draft?.length?config.railway_calibration_draft:(config.railway_calibration?.points || []);
  railPoints=stored.map(point=>({...point,image:[...point.image]}));
  railDraft=railPoints.length<2?{index:railPoints.length}:null;
  let busy=false, selection='image', markers=[];
  $('#app').innerHTML=`<div id="calibration-workspace"><div class="intro"><div><h1>Match railway points</h1><p>Match the same fixed spot on the rail in the camera image and map. Save each point, then repeat farther along the same straight rail.</p></div><button class="secondary" id="calibration-back">Back to settings</button></div>
    <section class="card rail-progress"><div id="rail-saved" aria-live="polite"></div><button class="secondary" id="rail-third">Add point C (optional)</button><button class="text-button" id="rail-reset">Camera moved / clear all points</button></section>
    <section class="card rail-guidance"><h2 id="rail-instruction"></h2><div class="rail-steps"><button class="secondary" id="rail-select-image">1. Choose camera point</button><button class="secondary" id="rail-select-map">2. Choose matching map point</button></div><p id="rail-feedback" role="status"></p></section>
    <div class="settings-grid">${previewHTML('calibration',false)}<section class="card"><h3>Matching map location</h3><div id="map" style="height:420px"></div><p>Click the same spot on the same rail. You can drag the selected map pin to adjust it.</p></section></div>
    <p>Two saved pairs establish the rail axis. A third pair can support future speed calibration. Recalibrate after moving or zooming the camera.</p>
    <div class="rail-save-bar"><div id="error" class="error-box" hidden></div><span id="rail-summary" aria-live="polite"></span><button id="rail-save-point">Save point A</button><button id="rail-save" class="secondary">Apply calibration</button></div></div>`;
  $('#calibration-back').onclick=returnTo;
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
