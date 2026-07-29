/* Megacap · panel de valoración
   Lee los JSON que GitHub Actions deja en data/ y los pinta con Chart.js. */

const COLOR = { GOOGL: '#2f5fcf', MSFT: '#c2571c', META: '#6248cf', AMZN: '#1f7a6b' };
const TINTA = '#11151c', TINTA2 = '#5b6472', LINEA = '#e9ecf0';
const RANGOS = { '1M': 21, '6M': 126, '1A': 252, '5A': 1260, 'Máx': Infinity };

const estado = { indice: null, cache: {}, ticker: 'GOOGL', rango: '1A' };
const graficas = {};
const $ = s => document.querySelector(s);

/* ------------------------------------------------------------- formateo -- */
const nf = (d = 2) => new Intl.NumberFormat('es-ES', { minimumFractionDigits: d, maximumFractionDigits: d });

const dinero = v => v == null ? '—' : '$' + nf(2).format(v);
const mult = v => (v == null || !isFinite(v) || v <= 0) ? '—' : nf(1).format(v) + '×';
const pct = v => v == null ? '—' : nf(1).format(v * 100) + ' %';
const pctDir = v => v == null ? '—' : (v >= 0 ? '+' : '') + nf(2).format(v) + ' %';

function grande(v) {
  if (v == null) return '—';
  const s = v < 0 ? '−' : '', a = Math.abs(v);
  if (a >= 1e12) return `${s}$${nf(2).format(a / 1e12)} B`;   // billones españoles
  if (a >= 1e9) return `${s}$${nf(1).format(a / 1e9)} mm`;
  if (a >= 1e6) return `${s}$${nf(0).format(a / 1e6)} M`;
  return s + '$' + nf(0).format(a);
}

const fechaES = iso => !iso ? '—'
  : new Date(iso).toLocaleString('es-ES', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });

/* --------------------------------------------------------------- datos -- */
async function json(ruta) {
  const r = await fetch(`${ruta}?v=${Date.now()}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${ruta}: ${r.status}`);
  return r.json();
}

async function empresa(t) {
  if (!estado.cache[t]) estado.cache[t] = await json(`data/${t}.json`);
  return estado.cache[t];
}

/* ------------------------------------------------------------ arranque -- */
async function iniciar() {
  try {
    estado.indice = await json('data/index.json');
  } catch {
    $('#app').innerHTML = `<p class="vacio">No se encuentra <code>data/index.json</code>.
      Ejecuta el workflow <b>Actualizar datos</b> en la pestaña Actions del repositorio y recarga.</p>`;
    return;
  }

  pintarSelector();
  pintarRangos();
  $('#btn-refrescar').addEventListener('click', refrescarPrecio);

  const sello = `Datos al ${fechaES(estado.indice.actualizado)}`;
  $('#sello').textContent = sello;
  $('#pie-sello').textContent = sello + ' (hora UTC del servidor).';

  await mostrar(estado.ticker);
  tablaComparativa();
}

function pintarSelector() {
  const nav = $('#selector');
  nav.innerHTML = '';
  estado.indice.tickers.forEach(t => {
    const b = document.createElement('button');
    b.className = 'pastilla';
    b.textContent = t;
    b.style.setProperty('--c', COLOR[t]);
    b.setAttribute('aria-pressed', String(t === estado.ticker));
    b.onclick = () => mostrar(t);
    nav.appendChild(b);
  });
}

function pintarRangos() {
  const cont = $('#rangos');
  cont.innerHTML = '';
  Object.keys(RANGOS).forEach(r => {
    const b = document.createElement('button');
    b.className = 'rango';
    b.textContent = r;
    b.setAttribute('aria-pressed', String(r === estado.rango));
    b.onclick = () => { estado.rango = r; pintarRangos(); graficaPrecio(estado.cache[estado.ticker]); };
    cont.appendChild(b);
  });
}

async function mostrar(t) {
  estado.ticker = t;
  document.documentElement.style.setProperty('--c', COLOR[t]);
  pintarSelector();

  const d = await empresa(t);
  portada(d);
  kpis(d);
  bandas(d);
  graficaPrecio(d);
  graficasFundamentales(d);
  noticias(d);
}

/* ------------------------------------------------------------- portada -- */
function portada(d) {
  const q = d.cotizacion || {};
  $('#p-ticker').textContent = d.ticker;
  $('#p-nombre').textContent = d.nombre;
  $('#p-precio').textContent = dinero(q.precio);

  const del = $('#p-delta');
  if (q.cambioPct == null) { del.textContent = ''; }
  else {
    del.textContent = `${q.cambio >= 0 ? '▲' : '▼'} ${dinero(Math.abs(q.cambio ?? 0))}  ${pctDir(q.cambioPct)}`;
    del.className = 'delta ' + (q.cambioPct >= 0 ? 'sube' : 'baja');
  }

  const avisos = (d.avisos || []).length ? ` · ${d.avisos.join('; ')}` : '';
  $('#p-fuente').textContent = `Precios vía ${d.fuentePrecios || '—'}${avisos}`;

  chispa(d);
}

function chispa(d) {
  const p = (d.precios || []).slice(-252);
  graficas.chispa?.destroy();
  if (!p.length) return;
  const sube = p.at(-1).c >= p[0].c;
  graficas.chispa = new Chart($('#chispa'), {
    type: 'line',
    data: {
      labels: p.map(x => x.f),
      datasets: [{
        data: p.map(x => x.c),
        borderColor: sube ? '#17724f' : '#b23a2e',
        borderWidth: 1.6, pointRadius: 0, fill: true, tension: .18,
        backgroundColor: sube ? 'rgba(23,114,79,.09)' : 'rgba(178,58,46,.09)',
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: { x: { display: false }, y: { display: false } },
      plugins: { legend: { display: false }, tooltip: tooltip(v => dinero(v)) },
    },
  });
}

/* ---------------------------------------------------------------- kpis -- */
function ultimo(d) { return (d.anual || []).at(-1) || {}; }

function kpis(d) {
  const q = d.cotizacion || {}, u = ultimo(d);
  const items = [
    ['Capitalización', grande(q.capitalizacion), 'valor de mercado'],
    ['PER', mult(q.per ?? u.per), 'beneficio TTM'],
    ['EV / EBITDA', mult(u.evEbitda), `ejercicio ${u.ejercicio || '—'}`],
    ['EV / FCF', mult(u.evFcl), 'free cash flow'],
    ['BPA diluido', dinero(u.bpa), `ejercicio ${u.ejercicio || '—'}`],
    ['Deuda neta', grande(u.deudaNeta), u.deudaNeta < 0 ? 'caja neta' : 'endeudamiento'],
    ['DN / EBITDA', mult(u.deudaNetaEbitda), 'apalancamiento'],
    ['Margen operativo', pct(u.margenOperativo), 'sobre ingresos'],
    ['ROIC', pct(u.roic), 'capital invertido'],
    ['Rango 52 sem.', `${dinero(q.min52)} – ${dinero(q.max52)}`, 'mínimo y máximo'],
  ];
  $('#kpis').innerHTML = items.map(([t, v, n]) =>
    `<div class="kpi"><p class="kpi-t">${t}</p><p class="kpi-v">${v}</p><p class="kpi-n">${n}</p></div>`
  ).join('');
}

/* ------------------------------------------------ bandas de valoración -- */
function bandas(d) {
  const serie = (d.anual || []).slice(-5);
  const defs = [
    ['PER', 'per'],
    ['EV / EBITDA', 'evEbitda'],
    ['EV / Free cash flow', 'evFcl'],
  ];

  $('#bandas').innerHTML = defs.map(([titulo, clave]) => {
    const vals = serie.map(x => x[clave]).filter(v => v != null && isFinite(v) && v > 0).sort((a, b) => a - b);
    const actual = clave === 'per' ? ((d.cotizacion || {}).per ?? ultimo(d).per) : ultimo(d)[clave];

    if (vals.length < 3 || actual == null || !isFinite(actual) || actual <= 0) {
      return `<div class="banda"><div class="banda-cab"><span class="banda-t">${titulo}</span>
        <span class="banda-v">${mult(actual)}</span></div>
        <p class="banda-lect">Sin histórico suficiente para situar el múltiplo en su rango.</p></div>`;
    }

    const min = vals[0], max = vals.at(-1);
    const med = vals.length % 2 ? vals[(vals.length - 1) / 2] : (vals[vals.length / 2 - 1] + vals[vals.length / 2]) / 2;
    const posicion = x => Math.min(100, Math.max(0, (x - min) / (max - min || 1) * 100));
    const percentil = Math.round(vals.filter(v => v <= actual).length / vals.length * 100);

    const lectura = actual > med
      ? `Cotiza <b>${nf(0).format((actual / med - 1) * 100)} % por encima</b> de su mediana de 5 años.`
      : `Cotiza <b>${nf(0).format((1 - actual / med) * 100)} % por debajo</b> de su mediana de 5 años.`;

    return `<div class="banda">
      <div class="banda-cab"><span class="banda-t">${titulo}</span><span class="banda-v">${mult(actual)}</span></div>
      <div class="pista">
        <span class="mediana" style="left:${posicion(med)}%"></span>
        <span class="marca" style="left:${posicion(actual)}%"></span>
      </div>
      <div class="pista-pies"><span>${mult(min)}</span><span>mediana ${mult(med)}</span><span>${mult(max)}</span></div>
      <p class="banda-lect">${lectura} Percentil ${percentil} del rango.</p>
    </div>`;
  }).join('');
}

/* ------------------------------------------------------------ gráficas -- */
function tooltip(fmt) {
  return {
    backgroundColor: '#11151c', padding: 10, cornerRadius: 6, displayColors: true,
    titleFont: { family: 'IBM Plex Mono', size: 11 },
    bodyFont: { family: 'IBM Plex Mono', size: 12 },
    callbacks: { label: c => ` ${c.dataset.label ? c.dataset.label + ': ' : ''}${fmt(c.parsed.y)}` },
  };
}

function ejes(fmtY) {
  return {
    x: { grid: { display: false }, ticks: { color: TINTA2, font: { family: 'IBM Plex Mono', size: 10 }, maxRotation: 0, autoSkipPadding: 22 } },
    y: { grid: { color: LINEA }, border: { display: false }, ticks: { color: TINTA2, font: { family: 'IBM Plex Mono', size: 10 }, callback: fmtY } },
  };
}

const baseOpts = (fmtY, fmtT) => ({
  responsive: true, maintainAspectRatio: false, animation: { duration: 320 },
  interaction: { mode: 'index', intersect: false },
  scales: ejes(fmtY),
  plugins: {
    legend: { display: false },
    tooltip: tooltip(fmtT || fmtY),
  },
});

function graficaPrecio(d) {
  const n = RANGOS[estado.rango];
  const p = n === Infinity ? (d.precios || []) : (d.precios || []).slice(-n);
  graficas.precio?.destroy();
  if (!p.length) return;

  const c = COLOR[d.ticker];
  graficas.precio = new Chart($('#g-precio'), {
    type: 'line',
    data: {
      labels: p.map(x => x.f),
      datasets: [{
        label: d.ticker, data: p.map(x => x.c),
        borderColor: c, borderWidth: 1.9, pointRadius: 0, tension: .12,
        fill: true, backgroundColor: ctx => degradado(ctx, c),
      }],
    },
    options: baseOpts(v => '$' + nf(0).format(v), v => dinero(v)),
  });
}

function degradado(ctx, color) {
  const { chart } = ctx, { ctx: g, chartArea: a } = chart;
  if (!a) return 'transparent';
  const grad = g.createLinearGradient(0, a.top, 0, a.bottom);
  grad.addColorStop(0, color + '2e');
  grad.addColorStop(1, color + '00');
  return grad;
}

function lineaAnual(id, d, series, fmtY, fmtT) {
  const a = d.anual || [];
  graficas[id]?.destroy();
  if (!a.length) return;
  graficas[id] = new Chart($('#' + id), {
    type: 'line',
    data: {
      labels: a.map(x => x.ejercicio),
      datasets: series.map(s => ({
        label: s.etiqueta,
        data: a.map(x => (s.valor(x) != null && isFinite(s.valor(x)) && s.valor(x) > 0) || s.permiteNegativo ? s.valor(x) : null),
        borderColor: s.color, backgroundColor: s.color,
        borderWidth: 2, pointRadius: 2.6, pointHoverRadius: 5, tension: .2, spanGaps: true,
      })),
    },
    options: {
      ...baseOpts(fmtY, fmtT),
      plugins: {
        ...baseOpts(fmtY, fmtT).plugins,
        legend: series.length > 1
          ? { display: true, position: 'bottom', labels: { boxWidth: 9, boxHeight: 9, usePointStyle: true, color: TINTA2, font: { family: 'Inter', size: 11 } } }
          : { display: false },
      },
    },
  });
}

function barrasAnual(id, d, series, fmtY, fmtT) {
  const a = d.anual || [];
  graficas[id]?.destroy();
  if (!a.length) return;
  graficas[id] = new Chart($('#' + id), {
    type: 'bar',
    data: {
      labels: a.map(x => x.ejercicio),
      datasets: series.map(s => ({
        label: s.etiqueta, data: a.map(s.valor),
        backgroundColor: s.color, borderRadius: 3, maxBarThickness: 26,
        type: s.tipo || 'bar', borderColor: s.color, borderWidth: s.tipo === 'line' ? 2 : 0,
        pointRadius: s.tipo === 'line' ? 2.4 : 0, tension: .2, order: s.tipo === 'line' ? 0 : 1,
      })),
    },
    options: {
      ...baseOpts(fmtY, fmtT),
      plugins: {
        ...baseOpts(fmtY, fmtT).plugins,
        legend: { display: true, position: 'bottom', labels: { boxWidth: 9, boxHeight: 9, usePointStyle: true, color: TINTA2, font: { family: 'Inter', size: 11 } } },
      },
    },
  });
}

function graficasFundamentales(d) {
  const c = COLOR[d.ticker];

  lineaAnual('g-per', d, [{ etiqueta: 'PER', valor: x => x.per, color: c }], v => nf(0).format(v) + '×', mult);
  lineaAnual('g-evebitda', d, [{ etiqueta: 'EV/EBITDA', valor: x => x.evEbitda, color: c }], v => nf(0).format(v) + '×', mult);
  lineaAnual('g-evfcl', d, [{ etiqueta: 'EV/FCF', valor: x => x.evFcl, color: c }], v => nf(0).format(v) + '×', mult);
  lineaAnual('g-deuda', d, [{ etiqueta: 'DN/EBITDA', valor: x => x.deudaNetaEbitda, color: c, permiteNegativo: true }],
    v => nf(1).format(v) + '×', mult);

  barrasAnual('g-ingresos', d, [
    { etiqueta: 'Ingresos', valor: x => x.ingresos, color: c + 'd0' },
    { etiqueta: 'EBITDA', valor: x => x.ebitda, color: TINTA + '80' },
  ], v => grande(v).replace('$', ''), grande);

  barrasAnual('g-bpa', d, [{ etiqueta: 'BPA diluido', valor: x => x.bpa, color: c + 'd0' }],
    v => '$' + nf(0).format(v), dinero);

  barrasAnual('g-fcl', d, [
    { etiqueta: 'Free cash flow', valor: x => x.fcl, color: c + 'd0' },
    { etiqueta: 'Capex', valor: x => x.capex, color: '#b23a2e99' },
  ], v => grande(v).replace('$', ''), grande);

  lineaAnual('g-margenes', d, [
    { etiqueta: 'Margen operativo', valor: x => x.margenOperativo, color: c, permiteNegativo: true },
    { etiqueta: 'ROIC', valor: x => x.roic, color: '#14555e', permiteNegativo: true },
  ], v => nf(0).format(v * 100) + '%', pct);
}

/* --------------------------------------------------------------- tabla -- */
function tablaComparativa() {
  const filas = estado.indice.resumen || [];
  const cols = [
    ['Precio', r => dinero(r.precio)],
    ['Día', r => `<span class="${r.cambioPct >= 0 ? 'sube' : 'baja'}">${pctDir(r.cambioPct)}</span>`],
    ['Capitalización', r => grande(r.capitalizacion)],
    ['PER', r => mult(r.per)],
    ['EV/EBITDA', r => mult(r.evEbitda)],
    ['EV/FCF', r => mult(r.evFcl)],
    ['BPA', r => dinero(r.bpa)],
    ['Deuda neta', r => grande(r.deudaNeta)],
    ['DN/EBITDA', r => mult(r.deudaNetaEbitda)],
    ['Margen op.', r => pct(r.margenOperativo)],
  ];
  $('#tabla').innerHTML =
    `<thead><tr><th>Compañía</th>${cols.map(c => `<th>${c[0]}</th>`).join('')}</tr></thead>
     <tbody>${filas.map(r => `<tr>
       <td><span class="punto" style="background:${COLOR[r.ticker]}"></span>${r.ticker}</td>
       ${cols.map(c => `<td>${c[1](r)}</td>`).join('')}
     </tr>`).join('')}</tbody>`;
}

/* ------------------------------------------------------------ noticias -- */
function noticias(d) {
  const n = d.noticias || [];
  $('#noticias').innerHTML = n.length
    ? n.map(x => `<li><a href="${x.url}" target="_blank" rel="noopener">
        <p class="n-t">${x.titulo}</p><p class="n-m">${x.medio || ''} · ${x.fecha || ''}</p></a></li>`).join('')
    : `<li><p class="vacio" style="padding:14px 16px">Sin titulares en la última descarga.</p></li>`;
}

/* ------------------------------------------- refresco de precio en vivo -- */
async function refrescarPrecio() {
  const btn = $('#btn-refrescar');
  btn.disabled = true; btn.textContent = 'Consultando…';
  const t = estado.ticker;
  try {
    const r = await fetch(`https://stooq.com/q/l/?s=${t.toLowerCase()}.us&f=sd2t2ohlcv&h&e=csv`, { cache: 'no-store' });
    const filas = (await r.text()).trim().split('\n');
    const cab = filas[0].split(','), val = filas[1].split(',');
    const fila = Object.fromEntries(cab.map((k, i) => [k.trim(), val[i]?.trim()]));
    const cierre = parseFloat(fila.Close), apertura = parseFloat(fila.Open);
    if (!isFinite(cierre)) throw new Error('sin cotización');

    const d = estado.cache[t];
    d.cotizacion = { ...d.cotizacion, precio: cierre };
    if (isFinite(apertura) && apertura > 0) {
      d.cotizacion.cambio = cierre - apertura;
      d.cotizacion.cambioPct = (cierre / apertura - 1) * 100;
    }
    portada(d);
    $('#p-fuente').textContent = `Precio en vivo vía Stooq · ${fila.Date || ''} ${fila.Time || ''} (variación frente a la apertura)`;
  } catch {
    $('#p-fuente').textContent = 'No se pudo leer la cotización en vivo. Se mantiene el último cierre descargado.';
  } finally {
    btn.disabled = false; btn.textContent = 'Refrescar precio';
  }
}

iniciar();
