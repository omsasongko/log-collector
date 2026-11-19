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
  // Fully local SVG line chart. No CDN, no Chart.js dependency.
  // Click a point -> drill down to that day/week/month and refresh IP codes.
  // -----------------------------------------------------------------
  const chartWrap = $('trendContainer');
  const svg = $('trendChart');
  if (svg && chartWrap) {
    let series = [];
    try { series = JSON.parse(chartWrap.dataset.trend || '[]'); }
    catch (_) { series = []; }
    if (!series.length) {
      svg.hidden = true;
      $('trendEmpty').hidden = false;
    } else {
      const NS = 'http://www.w3.org/2000/svg';
      const W = 900, H = 320, left = 45, right = 16, top = 16, bottom = 64;
      const pw = W - left - right, ph = H - top - bottom;
      const maxVal = Math.max(1, ...series.map(p => Number(p.count)));
      const niceMax = maxVal > 10 ? Math.ceil(maxVal / 4 / Math.pow(10, Math.floor(Math.log10(maxVal / 4)))) * Math.pow(10, Math.floor(Math.log10(maxVal / 4))) * 4 : Math.ceil(maxVal / 4) * 4 || 4;
      function el(tag, attrs = {}, text = '') {
        const n = document.createElementNS(NS, tag);
        for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v));
        if (text) n.textContent = text;
        return n;
      }
      const x = index => left + (series.length === 1 ? pw / 2 : (index / (series.length - 1)) * pw);
      const y = count => top + ph - (count / niceMax) * ph;
      const short = n => n >= 1e6 ? `${(n / 1e6).toFixed(1)}m` : n >= 1000 ? `${(n / 1000).toFixed(n >= 1e4 ? 0 : 1)}k` : String(Math.round(n));
      const defs = el('defs');
      const grad = el('linearGradient', {id:'trendGradient', x1:0, x2:0, y1:0, y2:1});
      grad.append(el('stop',{offset:'0%', 'stop-color':'#9584ef', 'stop-opacity':'.28'}));
      grad.append(el('stop',{offset:'100%', 'stop-color':'#9584ef', 'stop-opacity':'0'}));
      defs.append(grad); svg.append(defs);
      for (let i = 0; i <= 4; i++) {
        const val = niceMax * i / 4;
        const yy = y(val);
        svg.append(el('line',{x1:left,y1:yy,x2:W-right,y2:yy,class:'grid-line'}));
        svg.append(el('text',{x:left-10,y:yy+4,'text-anchor':'end',class:'tick'},short(val)));
      }
      const points = series.map((p,i) => [x(i), y(Number(p.count))]);
      const line = points.map(([px,py],i) => `${i?'L':'M'}${px.toFixed(2)},${py.toFixed(2)}`).join(' ');
      svg.append(el('path',{d:`${line} L${points[points.length-1][0].toFixed(2)},${(top+ph).toFixed(2)} L${points[0][0].toFixed(2)},${(top+ph).toFixed(2)} Z`,class:'trend-area'}));
      svg.append(el('path',{d:line,class:'trend-line'}));
      const labelStep = Math.max(2, Math.ceil(series.length / 6));
      series.forEach((p,i) => {
        const [px,py] = points[i];
        if (i % labelStep === 0 || i === series.length-1) {
          const anchor = i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle';
          svg.append(el('text',{x:px,y:H-18,'text-anchor':anchor,class:'tick'},p.label));
        }
        const pointLabel = el('text',{x:px,y:py-26,'text-anchor':'middle',class:'point-label'},'');
        pointLabel.append(el('tspan',{x:px,dy:0,class:'point-date'},p.label));
        pointLabel.append(el('tspan',{x:px,dy:'1.25em',class:'point-count'},Number(p.count).toLocaleString()));
        svg.append(pointLabel);
        const circle = el('circle',{cx:px,cy:py,r:Math.min(4,series.length>100?2:4),class:'trend-point','aria-label':`${p.label}: ${p.count} prints`,tabindex:'0'});
        circle.append(el('title',{},`${p.label}\n${Number(p.count).toLocaleString()} prints\nKlik untuk filter tanggal`));
        const choose = () => {
          range.value = 'custom';
          nativeFrom.value = p.start;
          nativeTo.value = p.end;
          displayFrom.value = fmtDateInput(new Date(p.start + 'T00:00:00'));
          displayTo.value = fmtDateInput(new Date(p.end + 'T00:00:00'));
          form.requestSubmit();
        };
        circle.addEventListener('click',choose);
        circle.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});
        svg.append(circle);
        const hit = el('circle',{cx:px,cy:py,r:8,class:'hit-point'});
        hit.append(el('title',{},`${p.label} • ${Number(p.count).toLocaleString()} prints`));
        hit.addEventListener('click',choose);
        svg.append(hit);
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
