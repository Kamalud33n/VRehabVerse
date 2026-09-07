/**
 * MedNova Chart Theme
 * Shared Chart.js styling: gradient fills, cascading reveal animation,
 * rounded bars, and a custom radial "gauge" widget — all pulled from
 * the app's CSS variables so charts always match the current theme.
 * ------------------------------------------------------------------
 * Include AFTER chart.umd.min.js and BEFORE the page's own script:
 *   <script src="/static/chart.umd.min.js"></script>
 *   <script src="/static/chart-theme.js"></script>
 */

(function () {
    const css = getComputedStyle(document.documentElement);
    const v = (name, fallback) => (css.getPropertyValue(name) || fallback).trim() || fallback;

    const Theme = {
        primary:   v('--primary', '#1B84FF'),
        primaryDk: v('--primary-dark', '#056EE9'),
        violet:    v('--violet', '#9F7AEA'),
        teal:      v('--secondary', '#63B3ED'),
        success:   v('--success', '#48BB78'),
        warning:   v('--warning', '#ED8936'),
        danger:    v('--danger', '#F56565'),
        info:      v('--info', '#4299E1'),
        orange:    v('--orange', '#ED8936'),
        text:      v('--text-primary', '#1F2937'),
        textMute:  v('--text-muted', '#718096'),
        border:    v('--border', '#E2E8F0'),
        surface:   v('--surface', '#FFFFFF'),
    };

    // Rotating palette used across every chart so colors stay consistent app-wide
    const PALETTE = [Theme.primary, Theme.violet, Theme.orange, Theme.teal, Theme.info, Theme.danger];

    // ---------- Global Chart.js defaults: smooth, cascading, unhurried ----------
    if (window.Chart) {
        Chart.defaults.font.family = "'Inter', sans-serif";
        Chart.defaults.color = Theme.textMute;
        Chart.defaults.animation = { duration: 900, easing: 'easeOutQuart' };
        Chart.defaults.animations.colors = false;
        Chart.defaults.transitions.active.animation.duration = 250;
    }

    // Cascading "waterfall" delay so bars/points/segments draw in one after another
    // instead of popping in all at once — this is what reads as "premium" motion.
    function cascade(baseDelay = 0, perItem = 45, perDataset = 120) {
        return {
            delay(ctx) {
                if (ctx.type !== 'data') return 0;
                return baseDelay + ctx.dataIndex * perItem + (ctx.datasetIndex || 0) * perDataset;
            },
        };
    }

    function tooltipStyle() {
        return {
            backgroundColor: '#0F172A',
            titleColor: '#fff',
            bodyColor: '#E2E8F0',
            borderColor: 'rgba(255,255,255,0.08)',
            borderWidth: 1,
            cornerRadius: 10,
            padding: 12,
            titleFont: { weight: '600', size: 12 },
            bodyFont: { size: 12 },
            displayColors: true,
            boxPadding: 4,
        };
    }

    // Vertical gradient fill for a canvas — top = solid color, fades to transparent
    function verticalGradient(ctx, chartArea, color, topAlpha = 0.32, bottomAlpha = 0.0) {
        const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        g.addColorStop(0, hexToRgba(color, topAlpha));
        g.addColorStop(1, hexToRgba(color, bottomAlpha));
        return g;
    }

    // Bar gradient — richer at top, softer at base, like the reference UI
    function barGradient(ctx, chartArea, color) {
        const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        g.addColorStop(0, hexToRgba(color, 1));
        g.addColorStop(1, hexToRgba(color, 0.55));
        return g;
    }

    function hexToRgba(hex, alpha) {
        hex = hex.replace('#', '');
        if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
        const r = parseInt(hex.substring(0, 2), 16);
        const g = parseInt(hex.substring(2, 4), 16);
        const b = parseInt(hex.substring(4, 6), 16);
        return `rgba(${r},${g},${b},${alpha})`;
    }

    function baseScales(tickCb, maxY) {
        return {
            y: {
                beginAtZero: true, max: maxY,
                grid: { color: 'rgba(15,23,42,0.045)', drawTicks: false },
                border: { display: false },
                ticks: { callback: tickCb, font: { size: 11 }, padding: 8 },
            },
            x: {
                grid: { display: false },
                border: { display: false },
                ticks: { font: { size: 11 }, maxRotation: 45 },
            },
        };
    }

    /**
     * Animated line chart with soft gradient fill under the curve.
     * opts: { label, color, tickCb, maxY, id }
     */
    function buildLineChart(id, labels, data, opts = {}) {
        const canvas = document.getElementById(id);
        if (!canvas) return null;
        const ctx = canvas.getContext('2d');
        const color = opts.color || Theme.primary;
        return new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: opts.label || '',
                    data,
                    borderColor: color,
                    backgroundColor: (c) => {
                        const { chartArea } = c.chart;
                        if (!chartArea) return hexToRgba(color, 0.15);
                        return verticalGradient(c.chart.ctx, chartArea, color);
                    },
                    tension: 0.4,
                    fill: true,
                    borderWidth: 3,
                    pointBackgroundColor: color,
                    pointBorderColor: Theme.surface,
                    pointBorderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 6,
                    pointHoverBorderWidth: 3,
                    pointHitRadius: 20,
                    cubicInterpolationMode: 'monotone',
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: cascade(0, 0, 0),
                animations: { y: { duration: 1000, easing: 'easeOutQuart' } },
                plugins: { legend: { display: false }, tooltip: tooltipStyle() },
                scales: baseScales(opts.tickCb, opts.maxY),
                interaction: { intersect: false, mode: 'index' },
            },
        });
    }

    /**
     * Animated bar chart, rounded top corners + top-to-bottom gradient,
     * with each bar cascading in shortly after the previous one.
     */
    function buildBarChart(id, labels, data, opts = {}) {
        const canvas = document.getElementById(id);
        if (!canvas) return null;
        const ctx = canvas.getContext('2d');
        const color = opts.color || Theme.primary;
        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: [{
                    label: opts.label || '',
                    data,
                    backgroundColor: (c) => {
                        const { chartArea } = c.chart;
                        if (!chartArea) return hexToRgba(color, 0.8);
                        return barGradient(c.chart.ctx, chartArea, color);
                    },
                    hoverBackgroundColor: color,
                    borderRadius: { topLeft: 8, topRight: 8, bottomLeft: 0, bottomRight: 0 },
                    borderSkipped: false,
                    maxBarThickness: 34,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: cascade(0, 55, 0),
                plugins: { legend: { display: false }, tooltip: tooltipStyle() },
                scales: baseScales(opts.tickCb, opts.maxY),
            },
        });
    }

    /** Multi-series grouped bar chart (e.g. old/new/total patients per day) */
    function buildGroupedBarChart(id, labels, series, opts = {}) {
        const canvas = document.getElementById(id);
        if (!canvas) return null;
        const ctx = canvas.getContext('2d');
        return new Chart(ctx, {
            type: 'bar',
            data: {
                labels,
                datasets: series.map((s, i) => ({
                    label: s.label,
                    data: s.data,
                    backgroundColor: (c) => {
                        const { chartArea } = c.chart;
                        const color = s.color || PALETTE[i % PALETTE.length];
                        if (!chartArea) return hexToRgba(color, 0.8);
                        return barGradient(c.chart.ctx, chartArea, color);
                    },
                    borderRadius: 6,
                    borderSkipped: false,
                    maxBarThickness: 16,
                })),
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                animation: cascade(0, 30, 150),
                plugins: {
                    legend: {
                        display: true, position: 'top', align: 'end',
                        labels: { usePointStyle: true, pointStyle: 'circle', boxWidth: 7, padding: 16, font: { size: 11.5 } },
                    },
                    tooltip: tooltipStyle(),
                },
                scales: baseScales(opts.tickCb, opts.maxY),
            },
        });
    }

    /**
     * Rounded, thick-ring doughnut with a live center label (value + caption)
     * rendered via canvas plugin so it re-centers automatically on resize.
     */
    function buildDoughnutChart(id, labels, data, opts = {}) {
        const canvas = document.getElementById(id);
        if (!canvas) return null;
        const ctx = canvas.getContext('2d');
        const colors = opts.colors || PALETTE;
        const total = data.reduce((a, b) => a + b, 0);

        const centerText = {
            id: 'centerText',
            afterDraw(chart) {
                if (!opts.centerLabel) return;
                const { ctx, chartArea: { top, bottom, left, right } } = chart;
                const cx = (left + right) / 2, cy = (top + bottom) / 2;
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = Theme.text;
                ctx.font = '700 22px Inter, sans-serif';
                ctx.fillText(String(total), cx, cy - 8);
                ctx.fillStyle = Theme.textMute;
                ctx.font = '500 11px Inter, sans-serif';
                ctx.fillText(opts.centerLabel, cx, cy + 14);
                ctx.restore();
            },
        };

        return new Chart(ctx, {
            type: 'doughnut',
            data: { labels, datasets: [{
                data, backgroundColor: colors, borderColor: Theme.surface,
                borderWidth: 3, borderRadius: 6, hoverOffset: 10, spacing: 2,
            }] },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '72%',
                animation: cascade(0, 90, 0),
                plugins: {
                    legend: { position: 'bottom', labels: { usePointStyle: true, pointStyle: 'circle', boxWidth: 7, padding: 14, font: { size: 11.5 } } },
                    tooltip: tooltipStyle(),
                },
            },
            plugins: opts.centerLabel ? [centerText] : [],
        });
    }

    /**
     * Radial "gauge" — a thick rounded progress ring with a big centered
     * percentage, matching the circular score widget in the reference UI.
     * Renders into a <canvas>; call refresh via `.setValue(v)` on the
     * returned handle to animate to a new value later.
     */
    function buildGauge(id, value, opts = {}) {
        const canvas = document.getElementById(id);
        if (!canvas) return null;
        const ctx = canvas.getContext('2d');
        const max = opts.max ?? 100;
        const color = opts.color || Theme.primary;
        const track = opts.trackColor || 'rgba(15,23,42,0.06)';

        const centerText = {
            id: 'gaugeCenterText',
            afterDraw(chart) {
                const { ctx, chartArea: { top, bottom, left, right } } = chart;
                const cx = (left + right) / 2, cy = (top + bottom) / 2;
                const live = chart.data.datasets[0].data[0];
                ctx.save();
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = Theme.text;
                ctx.font = '800 28px Inter, sans-serif';
                ctx.fillText(Math.round(live) + (opts.suffix ?? '%'), cx, cy - (opts.label ? 8 : 0));
                if (opts.label) {
                    ctx.fillStyle = Theme.textMute;
                    ctx.font = '600 11px Inter, sans-serif';
                    ctx.fillText(opts.label, cx, cy + 16);
                }
                ctx.restore();
            },
        };

        const chart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                datasets: [{
                    data: [value, Math.max(max - value, 0)],
                    backgroundColor: [color, track],
                    borderWidth: 0,
                    borderRadius: [{ outerStart: 8, outerEnd: 8, innerStart: 8, innerEnd: 8 }, 0],
                    circumference: 360,
                    rotation: -90,
                }],
            },
            options: {
                responsive: true, maintainAspectRatio: false, cutout: '76%',
                animation: { duration: 1100, easing: 'easeOutQuart' },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
            },
            plugins: [centerText],
        });

        return {
            chart,
            setValue(v) {
                chart.data.datasets[0].data = [v, Math.max(max - v, 0)];
                chart.update();
            },
        };
    }

    // Expose on window so page scripts can call MedNovaCharts.buildLineChart(...) etc.
    window.MedNovaCharts = {
        Theme, PALETTE, tooltipStyle, hexToRgba,
        buildLineChart, buildBarChart, buildGroupedBarChart, buildDoughnutChart, buildGauge,
    };
})();