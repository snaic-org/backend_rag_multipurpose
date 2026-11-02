// Chart.js wrappers that apply the data-viz rules: thin bars with 4px rounded
// data ends, 2px lines, hairline solid grid, one tooltip per X, a legend for
// two or more series, and a table view next to every chart.
import { h, icon, fmt } from './ui.js';

const live = new Set();

export function destroyCharts() {
  for (const chart of live) chart.destroy();
  live.clear();
}

export function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const STATUS = { good: '#0ca30c', warning: '#fab219', critical: '#d03b3b' };

function baseOptions({ stacked = false, horizontal = false, valueFormat = fmt.compact, tooltipTitle } = {}) {
  const grid = token('--grid');
  const muted = token('--text-3');
  const valueAxis = {
    stacked,
    beginAtZero: true,
    grid: { color: grid, drawTicks: false },
    border: { display: false },
    ticks: { color: muted, padding: 8, maxTicksLimit: 6, callback: (value) => valueFormat(value), font: { size: 11 } },
  };
  const categoryAxis = {
    stacked,
    grid: { display: false },
    border: { color: token('--axis') },
    ticks: { color: muted, padding: 6, autoSkip: true, maxRotation: 0, font: { size: 11 } },
  };
  return {
    responsive: true,
    maintainAspectRatio: false,
    indexAxis: horizontal ? 'y' : 'x',
    animation: { duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 700, easing: 'easeOutQuart' },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: token('--surface'),
        titleColor: token('--text-2'),
        bodyColor: token('--text'),
        borderColor: token('--hairline-strong'),
        borderWidth: 1,
        padding: 10,
        cornerRadius: 10,
        boxWidth: 10,
        boxHeight: 2,
        usePointStyle: false,
        titleFont: { weight: '500' },
        bodyFont: { weight: '600' },
        callbacks: {
          title: tooltipTitle,
          label: (context) => ` ${valueFormat(context.parsed[horizontal ? 'x' : 'y'])}  ${context.dataset.label || ''}`,
        },
      },
    },
    scales: horizontal ? { x: valueAxis, y: { ...categoryAxis, border: { display: false } } } : { x: categoryAxis, y: valueAxis },
  };
}

function barDataset(dataset, { stacked, isTop }) {
  return {
    maxBarThickness: 24,
    borderSkipped: 'start',
    // Rounded data end only on the outermost segment; a 2px surface gap between stacked segments.
    borderRadius: isTop ? { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 } : 0,
    borderColor: token('--chart-surface'),
    borderWidth: stacked ? { top: 2 } : 0,
    ...dataset,
  };
}

export function barChart(canvas, { labels, datasets, stacked = false, horizontal = false, valueFormat, tooltipTitle }) {
  const prepared = datasets.map((dataset, index) => {
    const bar = barDataset(dataset, { stacked, isTop: !stacked || index === datasets.length - 1 });
    if (horizontal) bar.borderRadius = { topRight: 4, bottomRight: 4, topLeft: 0, bottomLeft: 0 };
    return bar;
  });
  const chart = new window.Chart(canvas, { type: 'bar', data: { labels, datasets: prepared }, options: baseOptions({ stacked, horizontal, valueFormat, tooltipTitle }) });
  live.add(chart);
  return chart;
}

export function lineChart(canvas, { labels, datasets, valueFormat, tooltipTitle }) {
  const prepared = datasets.map((dataset) => ({
    borderWidth: 2,
    tension: 0.35,
    pointRadius: 0,
    pointHoverRadius: 5,
    pointHoverBorderWidth: 2,
    pointHoverBorderColor: token('--chart-surface'),
    fill: datasets.length === 1 ? { target: 'origin' } : false,
    backgroundColor: `${dataset.borderColor}1a`,
    ...dataset,
  }));
  const chart = new window.Chart(canvas, { type: 'line', data: { labels, datasets: prepared }, options: baseOptions({ valueFormat, tooltipTitle }) });
  live.add(chart);
  return chart;
}

// A chart card body: legend (2+ series), the canvas, and a table-view toggle.
export function chartPanel({ size = '', series = [], table, draw }) {
  const canvas = h('canvas', { role: 'img', 'aria-label': table.caption });
  const box = h('div', { class: `chart-box ${size}` }, canvas);
  const tableView = h('div', { class: 'data-table-view', hidden: true });
  const toggle = h('button', { class: 'button ghost table-toggle', type: 'button', 'aria-expanded': 'false' }, icon('table'), 'Table');
  toggle.addEventListener('click', () => {
    const show = tableView.hidden;
    tableView.hidden = !show;
    toggle.setAttribute('aria-expanded', String(show));
    if (show && !tableView.firstChild) tableView.append(simpleTable(table));
  });
  const legend = series.length >= 2
    ? h('div', { class: 'legend' }, series.map((item) => {
      const swatch = h('i', {});
      swatch.style.background = item.color;
      return h('span', {}, swatch, item.label);
    }))
    : '';
  const panel = h('div', {}, legend, box, tableView);
  panel.toggle = toggle;
  requestAnimationFrame(() => draw(canvas));
  return panel;
}

function simpleTable({ caption, headers, rows }) {
  return h('table', { class: 'table' },
    h('caption', { class: 'sr-only' }, caption),
    h('thead', {}, h('tr', {}, headers.map((label, index) => h('th', { class: index ? 'num' : '', scope: 'col' }, label)))),
    h('tbody', {}, rows.map((row) => h('tr', {}, row.map((cell, index) => h('td', { class: index ? 'num' : '' }, cell))))));
}
