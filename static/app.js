/* BLE→MQTT Bridge Web UI JavaScript */

const API = '';

// Tab switching
function initTabs() {
  document.querySelectorAll('nav.tabs button').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('nav.tabs button').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      btn.classList.add('active');
      const tab = document.getElementById(btn.dataset.tab);
      if (tab) tab.classList.add('active');
    });
  });
}

// Toast notifications
function toast(message, type = 'info') {
  const container = document.querySelector('.toast-container') || (() => {
    const el = document.createElement('div');
    el.className = 'toast-container';
    document.body.appendChild(el);
    return el;
  })();
  const t = document.createElement('div');
  t.className = `toast ${type}`;
  t.textContent = message;
  container.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// API helpers
async function api(method, path, body = null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(API + path, opts);
  return res.json();
}

// ── Devices Tab ──
async function loadDevices() {
  const data = await api('GET', '/api/devices');
  const tbody = document.getElementById('device-list');
  if (!tbody) return;
  tbody.innerHTML = '';
  for (const [mac, cfg] of Object.entries(data.devices || {})) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${mac}</code></td>
      <td>${cfg.name || ''}</td>
      <td>${cfg.location || ''}</td>
      <td>
        <select onchange="updateDeviceTemplate('${mac}', this.value)">
          ${renderTemplateOptions(cfg.template)}
        </select>
      </td>
      <td>
        <button class="btn small danger" onclick="removeDevice('${mac}')">Remove</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

function renderTemplateOptions(selected) {
  const templates = window._templates || [];
  return templates.map(t =>
    `<option value="${t.id}" ${t.id === selected ? 'selected' : ''}>${t.name}</option>`
  ).join('');
}

async function addDevice() {
  const mac = document.getElementById('new-mac').value.trim();
  const name = document.getElementById('new-name').value.trim();
  const location = document.getElementById('new-location').value.trim();
  const template = document.getElementById('new-template').value;
  if (!mac) return toast('MAC address required', 'error');
  await api('POST', '/api/devices', { mac, name, location, template });
  toast('Device added', 'success');
  loadDevices();
}

async function removeDevice(mac) {
  if (!confirm(`Remove device ${mac}?`)) return;
  await api('DELETE', `/api/devices/${mac}`);
  loadDevices();
}

async function updateDeviceTemplate(mac, template) {
  await api('PUT', `/api/devices/${mac}`, { template });
  toast(`Template updated for ${mac}`, 'success');
}

// ── Templates Tab ──
async function loadTemplates() {
  const data = await api('GET', '/api/templates');
  window._templates = data.templates || [];
  const tbody = document.getElementById('template-list');
  if (!tbody) return;
  tbody.innerHTML = '';
  for (const t of window._templates) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${t.name}</td>
      <td>${t.manufacturer || ''}</td>
      <td>${t.model || ''}</td>
      <td>${t.field_count}</td>
      <td>
        <button class="btn small secondary" onclick="exportTemplate('${t.id}')">Export</button>
        <button class="btn small danger" onclick="deleteTemplate('${t.id}')">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

async function importTemplate() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    const text = await file.text();
    const data = JSON.parse(text);
    const id = file.name.replace('.json', '');
    await api('POST', '/api/templates', { id, template: data });
    toast('Template imported', 'success');
    loadTemplates();
  };
  input.click();
}

async function exportTemplate(id) {
  const data = await api('GET', `/api/templates/${id}`);
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${id}.json`;
  a.click();
}

async function deleteTemplate(id) {
  if (!confirm(`Delete template ${id}?`)) return;
  await api('DELETE', `/api/templates/${id}`);
  loadTemplates();
}

// ── Scanner Tab ──
async function scanDevices() {
  const btn = document.getElementById('scan-btn');
  btn.disabled = true;
  btn.textContent = 'Scanning...';
  const timeout = document.getElementById('scan-timeout').value || 10;
  try {
    const data = await api('POST', '/api/scan', { timeout: parseInt(timeout) });
    renderScanResults(data.devices || []);
  } catch (e) {
    toast('Scan failed: ' + e.message, 'error');
  }
  btn.disabled = false;
  btn.textContent = 'Start Scan';
}

function renderScanResults(devices) {
  const tbody = document.getElementById('scan-results');
  tbody.innerHTML = '';
  for (const d of devices) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${d.address}</code></td>
      <td>${d.name}</td>
      <td>${d.rssi || ''}</td>
      <td>
        <select id="scan-template-${d.address.replace(/:/g, '_')}">
          <option value="">-- select --</option>
          ${renderTemplateOptions('')}
        </select>
      </td>
      <td>
        <button class="btn small" onclick="addScannedDevice('${d.address}', '${d.name}')">Add</button>
        <button class="btn small secondary" onclick="captureAndDecode('${d.address}')">Capture</button>
      </td>
    `;
    tbody.appendChild(tr);
  }
}

async function addScannedDevice(address, name) {
  const sel = document.getElementById(`scan-template-${address.replace(/:/g, '_')}`);
  const template = sel ? sel.value : '';
  if (!template) return toast('Select a template first', 'error');
  await api('POST', '/api/devices', { mac: address, name, template });
  toast(`Added ${name}`, 'success');
  loadDevices();
}

async function captureAndDecode(address) {
  const characteristic = prompt('Characteristic UUID to capture:');
  if (!characteristic) return;
  toast('Capturing packets (30s)...', 'info');
  try {
    const data = await api('POST', '/api/capture', {
      address,
      characteristic,
      duration: 30,
      max_packets: 25,
    });
    window._capture = data;
    // Switch to decoder tab
    document.querySelector('nav.tabs button[data-tab="decoder"]').click();
    loadCapture(data);
    toast(`Captured ${data.packets?.length || 0} packets`, 'success');
  } catch (e) {
    toast('Capture failed: ' + e.message, 'error');
  }
}

// ── Decoder Wizard ──
let decoderState = {
  packets: [],
  currentIndex: 0,
  fields: [],
  selectedFieldIndex: -1,
  clickMode: null, // 'start' or 'end'
};

function loadCapture(capture) {
  decoderState.packets = capture.packets || [];
  decoderState.currentIndex = 0;
  decoderState.fields = [];
  decoderState.selectedFieldIndex = -1;
  renderDecoder();
}

function renderDecoder() {
  renderPacketHeader();
  renderByteDiff();
  renderFieldTable();
}

function renderPacketHeader() {
  const el = document.getElementById('packet-view');
  if (!el || !decoderState.packets.length) return;

  const pkt = decoderState.packets[decoderState.currentIndex];
  const bytes = pkt.dec || [];
  const total = decoderState.packets.length;

  document.getElementById('packet-counter').textContent =
    `Packet ${decoderState.currentIndex + 1} of ${total}`;

  let html = '<div class="hex-view">';
  // Index row
  html += '<div>';
  for (let i = 0; i < bytes.length; i++) {
    html += `<span class="hex-index">${i}</span>`;
  }
  html += '</div>';
  // Hex row
  html += '<div>';
  for (let i = 0; i < bytes.length; i++) {
    const mapped = isMapped(i);
    const cls = mapped ? 'mapped' : '';
    html += `<span class="hex-byte ${cls}" data-idx="${i}" onclick="onByteClick(${i})">${bytes[i].toString(16).padStart(2, '0').toUpperCase()}</span>`;
  }
  html += '</div>';
  // Dec row
  html += '<div>';
  for (let i = 0; i < bytes.length; i++) {
    html += `<span class="hex-index">${bytes[i]}</span>`;
  }
  html += '</div>';
  html += '</div>';
  el.innerHTML = html;
}

function isMapped(idx) {
  return decoderState.fields.some(f => idx >= f.start_byte && idx <= f.end_byte);
}

function onByteClick(idx) {
  if (decoderState.selectedFieldIndex < 0) {
    toast('Select a field row first', 'warn');
    return;
  }
  const field = decoderState.fields[decoderState.selectedFieldIndex];
  if (!field._clickState || field._clickState === 'end') {
    field.start_byte = idx;
    field.end_byte = idx;
    field._clickState = 'start';
  } else {
    field.end_byte = Math.max(field.start_byte, idx);
    field._clickState = 'end';
  }
  renderDecoder();
}

function renderByteDiff() {
  const el = document.getElementById('diff-view');
  if (!el || decoderState.packets.length < 2) {
    if (el) el.innerHTML = '<p style="color: var(--text2)">Need 2+ packets for diff</p>';
    return;
  }

  const curr = decoderState.packets[decoderState.currentIndex].dec || [];
  const prevIdx = Math.max(0, decoderState.currentIndex - 1);
  const prev = decoderState.packets[prevIdx].dec || [];

  let html = '<div class="hex-view"><div>';
  for (let i = 0; i < curr.length; i++) {
    const changed = i >= prev.length || curr[i] !== prev[i];
    const cls = changed ? 'changed' : '';
    html += `<span class="hex-byte ${cls}" title="Byte ${i}">${curr[i].toString(16).padStart(2, '0').toUpperCase()}</span>`;
  }
  html += '</div></div>';
  el.innerHTML = html;
}

function renderFieldTable() {
  const tbody = document.getElementById('field-table-body');
  if (!tbody) return;
  tbody.innerHTML = '';
  decoderState.fields.forEach((f, i) => {
    const pkt = decoderState.packets[decoderState.currentIndex];
    const preview = pkt ? decodeFieldPreview(pkt.dec, f) : '';
    const selected = i === decoderState.selectedFieldIndex ? 'selected' : '';
    const tr = document.createElement('tr');
    tr.className = selected;
    tr.onclick = () => { decoderState.selectedFieldIndex = i; renderFieldTable(); };
    tr.innerHTML = `
      <td><input value="${f.name}" onchange="decoderState.fields[${i}].name=this.value"></td>
      <td><input type="number" value="${f.start_byte}" min="0" style="width:60px" onchange="decoderState.fields[${i}].start_byte=parseInt(this.value)"></td>
      <td><input type="number" value="${f.end_byte}" min="0" style="width:60px" onchange="decoderState.fields[${i}].end_byte=parseInt(this.value)"></td>
      <td>
        <select onchange="decoderState.fields[${i}].type=this.value">
          ${['uint8','int8','uint16_be','uint16_le','int16_be','int16_le','uint32_be','uint32_le','float32_be','float32_le','bool','ascii']
            .map(t => `<option ${f.type===t?'selected':''}>${t}</option>`).join('')}
        </select>
      </td>
      <td><input type="number" value="${f.scale}" step="0.01" style="width:70px" onchange="decoderState.fields[${i}].scale=parseFloat(this.value)"></td>
      <td><input value="${f.unit}" style="width:60px" onchange="decoderState.fields[${i}].unit=this.value"></td>
      <td><code>${preview}</code></td>
      <td><button class="btn small danger" onclick="event.stopPropagation();removeField(${i})">×</button></td>
    `;
    tbody.appendChild(tr);
  });
}

function addField() {
  decoderState.fields.push({
    name: 'New Field',
    start_byte: 0,
    end_byte: 0,
    type: 'uint8',
    scale: 1,
    unit: '',
    device_class: '',
    state_class: '',
    icon: '',
    key: 'field_' + decoderState.fields.length,
  });
  decoderState.selectedFieldIndex = decoderState.fields.length - 1;
  renderFieldTable();
}

function removeField(idx) {
  decoderState.fields.splice(idx, 1);
  if (decoderState.selectedFieldIndex >= decoderState.fields.length) {
    decoderState.selectedFieldIndex = decoderState.fields.length - 1;
  }
  renderDecoder();
}

function decodeFieldPreview(bytes, field) {
  if (!bytes || bytes.length === 0) return '';
  try {
    const start = field.start_byte;
    const end = field.end_byte + 1;
    const raw = bytes.slice(start, end);
    let val;
    const dv = new DataView(new Uint8Array(raw).buffer);
    switch (field.type) {
      case 'uint8': val = raw[0]; break;
      case 'int8': val = dv.getInt8(0); break;
      case 'bool': val = raw[0] !== 0; break;
      case 'uint16_be': val = dv.getUint16(0, false); break;
      case 'uint16_le': val = dv.getUint16(0, true); break;
      case 'int16_be': val = dv.getInt16(0, false); break;
      case 'int16_le': val = dv.getInt16(0, true); break;
      case 'uint32_be': val = dv.getUint32(0, false); break;
      case 'uint32_le': val = dv.getUint32(0, true); break;
      case 'float32_be': val = dv.getFloat32(0, false); break;
      case 'float32_le': val = dv.getFloat32(0, true); break;
      case 'ascii': val = String.fromCharCode(...raw).replace(/\0/g, ''); break;
      default: val = '?';
    }
    // Same arithmetic as TemplateField.decode in template.py: raw * scale + offset,
    // so a field definition carrying an offset (thermopro_tp960.json has
    // offset -39.785) previews the value the bridge would publish.
    //
    // Defensive alignment with the Python decoder, not a fix for an observed
    // symptom: the wizard has no offset input. decoderState.fields is only ever
    // [] or what addField() pushes, and addField() never sets offset, so
    // field.offset is undefined today and the ?? 0 keeps this branch behaving
    // exactly as it did before.
    const scale = field.scale ?? 1;
    const offset = field.offset ?? 0;
    if (typeof val === 'number' && (scale !== 1 || offset !== 0)) {
      val = (val * scale + offset).toFixed(2);
    }
    return String(val);
  } catch (e) {
    return 'err';
  }
}

function testDecode() {
  if (!decoderState.packets.length) return toast('No packets loaded', 'error');
  const el = document.getElementById('test-results');
  let html = '<table><thead><tr><th>Packet</th>';
  decoderState.fields.forEach(f => { html += `<th>${f.name}</th>`; });
  html += '</tr></thead><tbody>';

  decoderState.packets.forEach((pkt, pi) => {
    html += `<tr><td>#${pi + 1}</td>`;
    decoderState.fields.forEach(f => {
      const val = decodeFieldPreview(pkt.dec || [], f);
      const cls = isNaN(val) || val === 'err' ? 'error' : 'ok';
      html += `<td><span class="badge ${cls}">${val}</span></td>`;
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

function prevPacket() {
  if (decoderState.currentIndex > 0) {
    decoderState.currentIndex--;
    renderDecoder();
  }
}

function nextPacket() {
  if (decoderState.currentIndex < decoderState.packets.length - 1) {
    decoderState.currentIndex++;
    renderDecoder();
  }
}

async function generateTemplate() {
  const name = document.getElementById('tpl-name').value || 'Custom Device';
  const manufacturer = document.getElementById('tpl-manufacturer').value || '';
  const model = document.getElementById('tpl-model').value || '';
  const characteristic = document.getElementById('tpl-characteristic').value || '';
  const templateId = document.getElementById('tpl-id').value || name.toLowerCase().replace(/\s+/g, '_');

  const tpl = {
    template_version: 1,
    name,
    manufacturer,
    model,
    characteristic,
    fields: decoderState.fields.map(f => {
      const out = {
        key: f.key || f.name.toLowerCase().replace(/[^a-z0-9]/g, '_'),
        name: f.name,
        start_byte: f.start_byte,
        end_byte: f.end_byte,
        type: f.type,
      };
      if (f.scale !== 1) out.scale = f.scale;
      if (f.offset) out.offset = f.offset;
      if (f.unit) out.unit = f.unit;
      if (f.device_class) out.device_class = f.device_class;
      if (f.state_class) out.state_class = f.state_class;
      if (f.icon) out.icon = f.icon;
      return out;
    }),
  };

  // Download
  const blob = new Blob([JSON.stringify(tpl, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${templateId}.json`;
  a.click();

  // Optionally save to server
  try {
    await api('POST', '/api/templates', { id: templateId, template: tpl });
    toast('Template saved to server', 'success');
    loadTemplates();
  } catch (e) {
    toast('Downloaded (server save failed)', 'warn');
  }
}

function loadCaptureFromFile() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json';
  input.onchange = async (e) => {
    const file = e.target.files[0];
    const text = await file.text();
    const data = JSON.parse(text);
    loadCapture(data);
    toast(`Loaded ${data.packets?.length || 0} packets`, 'success');
  };
  input.click();
}

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
  initTabs();
  try {
    await loadTemplates();
    await loadDevices();
  } catch (e) {
    console.warn('Initial load failed:', e);
  }
});
