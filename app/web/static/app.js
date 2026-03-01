// app/web/static/app.js
async function apiGet(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(await r.text());
  return await r.json();
}
async function apiPut(url, body){
  const r = await fetch(url, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });
  if(!r.ok) throw new Error(await r.text());
  return await r.json();
}
async function apiPost(url, body){
  const r = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body)
  });
  if(!r.ok) throw new Error(await r.text());
  return await r.json();
}

const el = (id)=>document.getElementById(id);

function setMsg(text, kind){
  const m = el('msg');
  m.textContent = text || '';
  m.className = 'msg ' + (kind || '');
}

function fillSettings(s){
  el('inbox_dir').value = s.inbox_dir || '';
  el('output_dir').value = s.output_dir || '';
  el('review_dir').value = s.review_dir || '';
  el('log_dir').value = s.log_dir || '';
  el('interval_minutes').value = s.interval_minutes || 60;
  el('sender_candidates').value = s.sender_candidates || '';
  el('log_retention_days').value = s.log_retention_days || 7;

  el('bank_sender_candidates').value = s.bank_sender_candidates || '';
  el('bank_folder_name').value = s.bank_folder_name || 'Bank';

  el('year_policy').value = (s.year_policy || 'strict');
  el('year_relaxed_years').value = (s.year_relaxed_years ?? 2);
}

function fillStatus(st){
  const running = (st.is_running ?? st.running) ? true : false;
  el('st_running').textContent = running ? 'yes' : 'no';

  el('st_ts').textContent = st.last_run_ts || '-';
  el('st_dur').textContent = (st.last_run_duration_ms != null)
    ? (Math.round(st.last_run_duration_ms) + ' ms')
    : '-';

  const c = st.last_counts || {};
  el('c_success').textContent = c.success || 0;
  el('c_review').textContent = c.review || 0;
  el('c_ignored').textContent = c.ignored || 0;
  el('c_error').textContent = c.error || 0;

  const errs = (st.last_errors || []);
  el('st_errors').textContent = errs.length ? errs.join('\n') : '';

  const rr = st.last_review_reasons || {};
  const rrLines = Object.keys(rr).sort((a,b)=>rr[b]-rr[a]).map(k=>`${k}: ${rr[k]}`);
  el('st_review_reasons').textContent = rrLines.length ? rrLines.join('\n') : '';

  const samples = st.last_review_samples || [];
  el('st_review_samples').textContent = samples.length
    ? samples.map(x=>`${x.reason} | ${x.file}`).join('\n')
    : '';
}

function fmtBytes(n){
  const v = Number(n||0);
  if(v < 1024) return v + ' B';
  if(v < 1024*1024) return (v/1024).toFixed(1) + ' KB';
  if(v < 1024*1024*1024) return (v/1024/1024).toFixed(1) + ' MB';
  return (v/1024/1024/1024).toFixed(2) + ' GB';
}

function sanitize(s){
  return (s||'').replace(/[&<>"']/g, (c)=>({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

async function refreshReview(){
  const list = el('reviewList');
  if(!list) return;
  list.innerHTML = '<div class="hint">Lade…</div>';
  try{
    const data = await apiGet('/api/review/list?limit=50');
    const items = data.items || [];
    if(!items.length){
      list.innerHTML = '<div class="empty">Keine Dateien im Review-Ordner.</div>';
      return;
    }

    list.innerHTML = '';
    items.forEach(it=>{
      const div = document.createElement('div');
      div.className = 'review-item';

      const reason = it.reason || '-';
      const extracted = it.extracted_date || '';

      div.innerHTML = `
        <div class="review-main">
          <div class="review-title">${sanitize(it.filename)}</div>
          <div class="review-meta mono">${sanitize(it.mtime_ts)} · ${fmtBytes(it.size_bytes)}${extracted?(' · extracted: '+sanitize(extracted)) : ''}</div>
          <div class="review-reason">${sanitize(reason)}</div>
        </div>
        <div class="review-actions">
          <a class="btn" target="_blank" href="/api/review/file/${encodeURIComponent(it.filename)}">Öffnen</a>
          <input class="review-input mono" placeholder="YYYY-MM oder YYYY-MM-DD" value="${sanitize(extracted)}" />
          <input class="review-input" placeholder="Sender (optional)" value="${sanitize(it.sender || '')}" />
          <button class="btn primary">Apply</button>
        </div>
      `;

      const btn = div.querySelector('button.btn.primary');
      const dateInput = div.querySelectorAll('input.review-input')[0];
      const senderInput = div.querySelectorAll('input.review-input')[1];

      btn.addEventListener('click', async ()=>{
        const override_date = (dateInput.value || '').trim();
        const override_sender = (senderInput.value || '').trim();
        if(!override_date){
          alert('Bitte Datum eingeben: YYYY-MM oder YYYY-MM-DD');
          return;
        }
        btn.disabled = true;
        btn.textContent = '…';
        try{
          const res = await apiPost('/api/review/apply', {
            filename: it.filename,
            override_date,
            override_sender: override_sender || null,
          });
          if(!res.ok){
            alert(res.message || 'Apply fehlgeschlagen');
          }
          await refresh();
          await refreshReview();
        }catch(e){
          alert('Apply fehlgeschlagen: ' + e.message);
        }finally{
          btn.disabled = false;
          btn.textContent = 'Apply';
        }
      });

      list.appendChild(div);
    });

  }catch(e){
    list.innerHTML = '<div class="empty">Review-Liste konnte nicht geladen werden: ' + sanitize(e.message) + '</div>';
  }
}

async function refresh(){
  try{
    const s = await apiGet('/api/settings');
    fillSettings(s);
  }catch(e){
    setMsg('Settings laden fehlgeschlagen: ' + e.message, 'err');
  }
  try{
    const st = await apiGet('/api/status');
    fillStatus(st);
  }catch(e){
    setMsg('Status laden fehlgeschlagen: ' + e.message, 'err');
  }
}

el('saveBtn').addEventListener('click', async ()=>{
  setMsg('', '');
  try{
    const body = {
      inbox_dir: el('inbox_dir').value.trim(),
      output_dir: el('output_dir').value.trim(),
      review_dir: el('review_dir').value.trim(),
      log_dir: el('log_dir').value.trim(),
      interval_minutes: parseInt(el('interval_minutes').value || '60', 10),
      sender_candidates: el('sender_candidates').value || '',
      log_retention_days: parseInt(el('log_retention_days').value || '7', 10),

      bank_sender_candidates: el('bank_sender_candidates').value || '',
      bank_folder_name: el('bank_folder_name').value.trim() || 'Bank',

      year_policy: (el('year_policy').value || 'strict').trim(),
      year_relaxed_years: parseInt(el('year_relaxed_years').value || '2', 10),
    };
    await apiPut('/api/settings', body);
    setMsg('Gespeichert.', 'ok');
    await refresh();
    await refreshReview();
  }catch(e){
    setMsg('Speichern fehlgeschlagen: ' + e.message, 'err');
  }
});

el('runBtn').addEventListener('click', async ()=>{
  setMsg('', '');
  try{
    const res = await apiPost('/api/run', {reason:'manual'});
    setMsg(res.message || 'Run ausgelöst.', res.ok ? 'ok' : 'err');
    setTimeout(async ()=>{ await refresh(); await refreshReview(); }, 800);
  }catch(e){
    setMsg('Run fehlgeschlagen: ' + e.message, 'err');
  }
});

el('reviewRefreshBtn')?.addEventListener('click', refreshReview);

// Folder picker
let pickerTarget = null;
let pickerPath = '/media';

function openPicker(targetId){
  pickerTarget = targetId;
  pickerPath = '/media';
  el('picker').classList.remove('hidden');
  loadPicker(pickerPath);
}
function closePicker(){
  el('picker').classList.add('hidden');
  pickerTarget = null;
}

async function loadPicker(path){
  const data = await apiGet('/api/browse?path=' + encodeURIComponent(path));
  pickerPath = data.cwd || data.current || '/media';
  el('picker-path').textContent = pickerPath;

  const list = el('picker-list');
  list.innerHTML = '';
  (data.entries || []).forEach(ent=>{
    const div = document.createElement('div');
    div.className = 'entry';
    div.innerHTML = `<div class="name">📁 ${sanitize(ent.name)}</div><div class="meta">${sanitize(ent.path)}</div>`;
    div.addEventListener('click', ()=>loadPicker(ent.path));
    list.appendChild(div);
  });
}

document.querySelectorAll('[data-pick]').forEach(btn=>{
  btn.addEventListener('click', ()=>openPicker(btn.dataset.pick));
});

el('picker-close')?.addEventListener('click', closePicker);
el('picker-up')?.addEventListener('click', async ()=>{
  if(!pickerPath) return;
  const p = pickerPath.replace(/\/+$/,'');
  if(p === '/media') return;
  const up = p.substring(0, p.lastIndexOf('/')) || '/media';
  await loadPicker(up);
});
el('picker-select')?.addEventListener('click', ()=>{
  if(!pickerTarget) return;
  el(pickerTarget).value = pickerPath;
  closePicker();
});

(async ()=>{
  await refresh();
  await refreshReview();
  setInterval(async ()=>{
    try{
      const st = await apiGet('/api/status');
      fillStatus(st);
    }catch(e){}
  }, 2000);

  setInterval(async ()=>{
    // review queue doesn't need 2s refresh, keep it light
    try{ await refreshReview(); }catch(e){}
  }, 15000);
})();
