(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const form = $('filterForm');
  const range = $('range');
  const ipInput = $('ip_code');

  function fmtDateInput(d) {
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yyyy = d.getFullYear();
    return `${dd}/${mm}/${yyyy}`;
  }
  const nativeFrom = $('date_from');
  const nativeTo = $('date_to');
  const displayFrom = $('date_from_display');
  const displayTo = $('date_to_display');
  [nativeFrom, nativeTo].forEach((native, idx) => {
    if (!native) return;
    const display = idx === 0 ? displayFrom : displayTo;
    native.addEventListener('change', () => {
      if (native.valueAsDate) {
        display.value = fmtDateInput(native.valueAsDate);
      } else {
        display.value = '';
      }
      range.value = 'custom';
    });
    // Clicking the visible display input opens the hidden native date picker
    if (display) {
      display.addEventListener('click', (e) => {
        e.preventDefault();
        try { native.showPicker(); } catch (_) { native.click(); }
      });
    }
  });
  range?.addEventListener('change', () => {
    if (range.value === 'all') {
      nativeFrom.value = ''; displayFrom.value = '';
      nativeTo.value = ''; displayTo.value = '';
    }
    form.requestSubmit();
  });

  const sidebar = $('sidebar');
  const backdrop = $('backdrop');
  $('menuBtn')?.addEventListener('click', () => {
    sidebar.classList.add('open'); backdrop.classList.add('visible');
  });
  backdrop?.addEventListener('click', () => {
    sidebar.classList.remove('open'); backdrop.classList.remove('visible');
  });
  sidebar?.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    sidebar.classList.remove('open'); backdrop.classList.remove('visible');
  }));
  document.querySelectorAll('.ip-row').forEach(button => {
    button.addEventListener('click', () => { ipInput.value = button.dataset.ip || ''; form.requestSubmit(); });
  });

  // -----------------------------------------------------------------
  // SVG line chart with multi-shift support.
  // shift='all' draws 3 lines; single shift draws 1 line.
  // -----------------------------------------------------------------
  const chartWrap = $('trendContainer');
  const svg = $('trendChart');
  if (svg && chartWrap) {
    const shiftMode = chartWrap.dataset.shift || 'all';
    let rawTrend;
    try { rawTrend = JSON.parse(chartWrap.dataset.trend || '{}'); } catch (_) { rawTrend = {}; }
    const SC = {'1':'#7061e8','2':'#e8a735','3':'#38b87c'};
    const SN = {'1':'Shift 1','2':'Shift 2','3':'Shift 3'};
    let sMap = {};
    if (shiftMode === 'all' && rawTrend && typeof rawTrend === 'object' && !Array.isArray(rawTrend)) sMap = rawTrend;
    else if (Array.isArray(rawTrend)) sMap[shiftMode] = rawTrend;
    const allEmpty = Object.values(sMap).every(s => !s || !s.length);
    if (allEmpty) {
      svg.hidden = true;
      $('trendEmpty').hidden = false;
    } else {
      const NS = 'http://www.w3.org/2000/svg';
      const W = 900, H = 320, L = 45, R = 16, T = 16, B = 64;
      const pw = W - L - R, ph = H - T - B;
      function el(tag, a = {}, t = '') { const n = document.createElementNS(NS, tag); for (const [k,v] of Object.entries(a)) n.setAttribute(k, String(v)); if (t) n.textContent = t; return n; }
      const ref = Object.values(sMap).find(s => s && s.length) || [];
      const allC = Object.values(sMap).flatMap(s => (s||[]).map(p => Number(p.count)));
      const mx = Math.max(1, ...allC);
      const nm = mx > 10 ? Math.ceil(mx/4/Math.pow(10,Math.floor(Math.log10(mx/4))))*Math.pow(10,Math.floor(Math.log10(mx/4)))*4 : Math.ceil(mx/4)*4||4;
      const xp = i => L + (ref.length === 1 ? pw/2 : (i/(ref.length-1))*pw);
      const yp = c => T + ph - (Number(c)/nm)*ph;
      const short = n => n>=1e6?`${(n/1e6).toFixed(1)}m`:n>=1000?`${(n/1000).toFixed(n>=1e4?0:1)}k`:String(Math.round(n));
      const defs = el('defs');
      ['1','2','3'].forEach(sk => { const g = el('linearGradient',{id:`shiftGrad${sk}`,x1:0,x2:0,y1:0,y2:1}); g.append(el('stop',{offset:'0%','stop-color':SC[sk],'stop-opacity':'.15'})); g.append(el('stop',{offset:'100%','stop-color':SC[sk],'stop-opacity':'0'})); defs.append(g); });
      svg.append(defs);
      for (let i = 0; i <= 4; i++) { const v = nm*i/4, yy = yp(v); svg.append(el('line',{x1:L,y1:yy,x2:W-R,y2:yy,class:'grid-line'})); svg.append(el('text',{x:L-10,y:yy+4,'text-anchor':'end',class:'tick'},short(v))); }
      const ls = Math.max(2, Math.ceil(ref.length/6));
      ref.forEach((p,i) => { if (i%ls===0||i===ref.length-1) { const a = i===0?'start':i===ref.length-1?'end':'middle'; svg.append(el('text',{x:xp(i),y:H-18,'text-anchor':a,class:'tick'},p.label)); } });
      const sKeys = Object.keys(sMap).filter(k => sMap[k]&&sMap[k].length).sort();
      const isSingle = sKeys.length === 1;
      sKeys.forEach(sk => {
        const s = sMap[sk], cls = `shift${sk}`;
        const pts = s.map((p,i) => [xp(i),yp(Number(p.count))]);
        const ld = pts.map(([px,py],i)=>`${i?'L':'M'}${px.toFixed(2)},${py.toFixed(2)}`).join(' ');
        svg.append(el('path',{d:`${ld} L${pts[pts.length-1][0].toFixed(2)},${(T+ph).toFixed(2)} L${pts[0][0].toFixed(2)},${(T+ph).toFixed(2)} Z`,class:`trend-area ${cls}`}));
        svg.append(el('path',{d:ld,class:`trend-line ${cls}`}));
      });
      sKeys.forEach(sk => {
        const s = sMap[sk], cls = `shift${sk}`, sn = SN[sk]||'';
        s.forEach((p,i) => {
          const px = xp(i), py = yp(Number(p.count));
          if (isSingle && s.length <= 31) { const lb = el('text',{x:px,y:py-26,'text-anchor':'middle',class:'point-label'},''); lb.append(el('tspan',{x:px,dy:0,class:'point-date'},p.label)); lb.append(el('tspan',{x:px,dy:'1.25em',class:'point-count'},Number(p.count).toLocaleString())); svg.append(lb); }
          const c = el('circle',{cx:px,cy:py,r:Math.min(4,s.length>100?2:4),class:`trend-point ${cls}`,'aria-label':`${sn} ${p.label}: ${p.count}`,tabindex:'0'});
          c.append(el('title',{},`${sn} • ${p.label}\n${Number(p.count).toLocaleString()} prints`));
          const choose = () => { range.value='custom'; nativeFrom.value=p.start; nativeTo.value=p.end; displayFrom.value=fmtDateInput(new Date(p.start+'T00:00:00')); displayTo.value=fmtDateInput(new Date(p.end+'T00:00:00')); form.requestSubmit(); };
          c.addEventListener('click',choose);
          c.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose();}});
          svg.append(c);
          const h = el('circle',{cx:px,cy:py,r:8,class:'hit-point'});
          h.append(el('title',{},`${sn} • ${p.label} • ${Number(p.count).toLocaleString()} prints`));
          h.addEventListener('click',choose); svg.append(h);
        });
      });
    }
  }

  // -----------------------------------------------------------------
  // Paginated IP CODE modal; always reads the same dashboard filters.
  // -----------------------------------------------------------------
  const modal = $('ipModal');
  const config = $('dashboardConfig');
  let ipPage = 1, ipPages = 1;
  const close = () => { modal.hidden = true; document.body.style.overflow = ''; };
  const open = () => { modal.hidden = false; document.body.style.overflow = 'hidden'; loadIpPage(1); };
  $('openIpModal')?.addEventListener('click', open);
  $('closeIpModal')?.addEventListener('click', close);
  modal?.addEventListener('click', e => { if (e.target === modal) close(); });
  document.addEventListener('keydown',e=>{ if (e.key==='Escape' && modal && !modal.hidden) close(); });
  $('ipPrevious')?.addEventListener('click',()=>loadIpPage(ipPage-1));
  $('ipNext')?.addEventListener('click',()=>loadIpPage(ipPage+1));
  async function loadIpPage(page) {
    if (!config || page < 1 || page > ipPages && ipPage !== 1) return;
    const status = $('ipModalStatus');
    const tbody = $('ipModalBody');
    status.textContent = 'Memuat data dari SQL Server…';
    tbody.replaceChildren();
    $('ipPrevious').disabled = true; $('ipNext').disabled = true;
    try {
      const filters = JSON.parse(config.dataset.query || '{}');
      const url = new URL(config.dataset.api, window.location.href);
      for (const [key,value] of Object.entries(filters)) if (value !== '') url.searchParams.set(key,value);
      url.searchParams.set('ip_page', String(page));
      const response = await fetch(url.toString(),{headers:{Accept:'application/json'}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Gagal memuat IP CODE');
      ipPage = data.page; ipPages = data.pages;
      status.textContent = `${Number(data.total_codes).toLocaleString()} IP CODE berbeda dalam filter`;
      $('ipPagination').textContent = `Halaman ${data.page} / ${data.pages}`;
      $('ipPrevious').disabled = data.page <= 1; $('ipNext').disabled = data.page >= data.pages;
      if (!data.items.length) {
        const row = tbody.insertRow(); const cell = row.insertCell(); cell.colSpan=3; cell.textContent='Tidak ada IP CODE.';
      }
      for (const item of data.items) {
        const row = tbody.insertRow();
        row.insertCell().textContent = item.ip_code;
        row.insertCell().textContent = Number(item.prints).toLocaleString();
        const cell = row.insertCell(); const button = document.createElement('button');
        button.textContent = 'Lihat logs'; button.type = 'button';
        button.addEventListener('click', () => { ipInput.value = item.ip_code; close(); form.requestSubmit(); });
        cell.append(button);
      }
    } catch (error) {
      status.textContent = `Gagal memuat: ${error.message}`;
    }
  }
})();
