(function () {
    'use strict';

    var PAYLOAD_ID = 'application-analytics-data';
    var THEME = {
        teal: '#2F5D73',
        gold: '#B4853A',
        ink: '#17303A',
        success: '#2F7A5B',
        danger: '#B54747',
        muted: '#9AA7AD',
        grid: 'rgba(23, 48, 58, 0.08)',
        tick: '#46586B',
        tealFill: 'rgba(47, 93, 115, 0.12)'
    };

    var chartjsPromise = null;
    var chartjsFailed = false;
    var chartjsErrorLogged = false;
    var charts = [];

    function loadChartJs(url) {
        if (window.Chart) return Promise.resolve(window.Chart);
        if (chartjsFailed || !url) return Promise.reject(new Error('Chart.js unavailable'));
        if (!chartjsPromise) {
            chartjsPromise = new Promise(function (resolve, reject) {
                var script = document.createElement('script');
                script.src = url;
                script.async = true;
                script.onload = function () {
                    if (window.Chart) resolve(window.Chart);
                    else reject(new Error('Chart.js did not register'));
                };
                script.onerror = function () { reject(new Error('Chart.js script failed')); };
                document.head.appendChild(script);
            }).catch(function (error) {
                chartjsFailed = true;
                chartjsPromise = null;
                throw error;
            });
        }
        return chartjsPromise;
    }

    function destroyCharts() {
        charts.forEach(function (chart) {
            try { chart.destroy(); } catch (error) { /* canvas already detached */ }
        });
        charts = [];
    }

    function cardTitle(canvasId) {
        var canvas = document.getElementById(canvasId);
        var body = canvas && canvas.closest('.ad-card-body');
        var heading = body ? body.querySelector('h6') : null;
        return heading ? heading.textContent.trim() : '';
    }

    function buildCharts(Chart, data) {
        var rtl = document.documentElement.getAttribute('dir') === 'rtl';
        var fontFamily = window.getComputedStyle(document.body).fontFamily;
        if (fontFamily) Chart.defaults.font.family = fontFamily;
        Chart.defaults.color = THEME.tick;

        var legend = { rtl: rtl, labels: { textDirection: rtl ? 'rtl' : 'ltr' } };
        var tooltip = { rtl: rtl };

        function make(id, config) {
            var canvas = document.getElementById(id);
            if (!canvas) return;
            config.options = config.options || {};
            charts.push(new Chart(canvas, config));
        }

        var modeLabels = data.study_mode.labels || [];

        make('analytics-series-chart', {
            type: 'bar',
            data: {
                labels: data.series.labels,
                datasets: [
                    { label: modeLabels[0] || '', data: data.series.online, backgroundColor: THEME.teal, stack: 'applications' },
                    { label: modeLabels[1] || '', data: data.series.offline, backgroundColor: THEME.gold, stack: 'applications' }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: legend, tooltip: tooltip },
                scales: {
                    x: { stacked: true, grid: { display: false }, ticks: { color: THEME.tick, autoSkip: true, maxTicksLimit: 14 } },
                    y: { stacked: true, beginAtZero: true, ticks: { color: THEME.tick, precision: 0 }, grid: { color: THEME.grid } }
                }
            }
        });

        make('analytics-cumulative-chart', {
            type: 'line',
            data: {
                labels: data.series.labels,
                datasets: [{
                    label: cardTitle('analytics-cumulative-chart'),
                    data: data.series.cumulative,
                    borderColor: THEME.teal,
                    backgroundColor: THEME.tealFill,
                    fill: true,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: tooltip },
                scales: {
                    x: { grid: { display: false }, ticks: { color: THEME.tick, autoSkip: true, maxTicksLimit: 14 } },
                    y: { beginAtZero: true, ticks: { color: THEME.tick, precision: 0 }, grid: { color: THEME.grid } }
                }
            }
        });

        make('analytics-status-chart', {
            type: 'doughnut',
            data: {
                labels: data.status.labels,
                datasets: [{
                    data: data.status.values,
                    backgroundColor: [THEME.gold, THEME.success, THEME.danger],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '55%',
                plugins: { legend: legend, tooltip: tooltip }
            }
        });

        make('analytics-mode-chart', {
            type: 'doughnut',
            data: {
                labels: modeLabels,
                datasets: [{
                    data: data.study_mode.values,
                    backgroundColor: [THEME.teal, THEME.gold, THEME.muted],
                    borderWidth: 0
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '55%',
                plugins: { legend: legend, tooltip: tooltip }
            }
        });

        make('analytics-city-chart', {
            type: 'bar',
            data: {
                labels: data.cities.labels,
                datasets: [{
                    label: cardTitle('analytics-city-chart'),
                    data: data.cities.values,
                    backgroundColor: THEME.teal
                }]
            },
            options: {
                indexAxis: 'y',
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: tooltip },
                scales: {
                    x: { beginAtZero: true, ticks: { color: THEME.tick, precision: 0 }, grid: { color: THEME.grid } },
                    y: { grid: { display: false }, ticks: { color: THEME.tick } }
                }
            }
        });

        make('analytics-weekday-chart', {
            type: 'bar',
            data: {
                labels: data.weekdays.labels,
                datasets: [{
                    label: cardTitle('analytics-weekday-chart'),
                    data: data.weekdays.values,
                    backgroundColor: THEME.ink
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false }, tooltip: tooltip },
                scales: {
                    x: { grid: { display: false }, ticks: { color: THEME.tick } },
                    y: { beginAtZero: true, ticks: { color: THEME.tick, precision: 0 }, grid: { color: THEME.grid } }
                }
            }
        });
    }

    function init() {
        var payloadNode = document.getElementById(PAYLOAD_ID);
        if (!payloadNode) {
            destroyCharts();
            return;
        }
        var host = document.querySelector('#content[data-chartjs-url]');
        if (!host || payloadNode.dataset.analyticsReady === 'true') return;

        var data;
        try {
            data = JSON.parse(payloadNode.textContent);
        } catch (error) {
            return;
        }
        payloadNode.dataset.analyticsReady = 'true';

        loadChartJs(host.dataset.chartjsUrl).then(function (Chart) {
            destroyCharts();
            buildCharts(Chart, data);
        }).catch(function (error) {
            if (!chartjsErrorLogged) {
                chartjsErrorLogged = true;
                console.error('Application analytics charts unavailable:', error);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', init);
    document.body.addEventListener('htmx:after:swap', init);
})();