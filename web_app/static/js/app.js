const INDEX_WHEEL_ZOOM_STEP = 0.08;
const INDEX_WHEEL_GESTURE_DELAY = 90;
const INDEX_MIN_VISIBLE_BARS = 16;
const INDEX_MAX_VISIBLE_BARS = 2500;

const state = {
    dashboard: null,
    indicators: [],
    selectedSignal: "",
    signalAssetType: "stock",
    stockPage: 1,
    stockPageSize: 20,
    stockTotal: 0,
    stockGroupId: null,
    stockGroups: [],
    etfPage: 1,
    etfPageSize: 20,
    etfTotal: 0,
    etfGroupId: null,
    etfGroups: [],
    groupAssetType: null,
    assignment: null,
    chartPoints: [],
    chartItem: null,
    indexItems: [],
    indexChartPoints: [],
    indexChartItem: null,
    indexChart: null,
    indexChartRequestId: 0,
    indexChartWheelHandler: null,
    indexChartWheelTimer: null,
    indexChartWheelDelta: 0,
    indexHistoryLoading: false,
    indexHistoryExhausted: false,
    indexPollTimer: null,
    marketOpen: false,
    taskPollTimer: null,
    tasks: {},
    currentView: "overview",
    selectedErrorRun: null,
    errorDetail: null,
    databaseSettings: null,
    confirmationResolver: null,
    dailyChartCache: new Map(),
    dailyPreviewTimer: null,
    dailyPreviewAnchor: null,
    dailyPreviewPointer: { x: 0, y: 0 },
    dailyPreviewChart: null,
    dailyChart: null,
    dailyChartRequestId: 0,
};

const viewMeta = {
    overview: ["MARKET INTELLIGENCE", "监控总览"],
    "daily-custom": ["DAILY STRATEGIES", "日线策略 · 自定义策略"],
    "daily-other": ["DAILY STRATEGIES", "日线策略 · 其他策略"],
    "minute-custom": ["MINUTE STRATEGIES", "分钟级策略 · 自定义策略"],
    "minute-other": ["MINUTE STRATEGIES", "分钟级策略 · 其他策略"],
    stocks: ["STOCK UNIVERSE", "股票列表"],
    etfs: ["ETF UNIVERSE", "ETF列表"],
    runs: ["AUTOMATION", "任务记录"],
    settings: ["PREFERENCES", "系统设置"],
};

const toneColors = {
    emerald: "var(--green)",
    cyan: "var(--cyan)",
    amber: "var(--amber)",
    violet: "var(--violet)",
    rose: "var(--red)",
    blue: "var(--blue)",
};

document.addEventListener("DOMContentLoaded", () => {
    bindNavigation();
    bindTheme();
    bindActions();
    bindFilters();
    loadDashboard();
    window.addEventListener("resize", debounce(() => {
        drawChart(state.chartPoints, state.chartItem);
        applyDailyPreviewChartTheme();
        applyDailyChartTheme();
    }, 180));
    document.addEventListener("visibilitychange", () => {
        if (document.hidden) cancelIndexPoll();
        else scheduleIndexPoll(1000);
    });
    scheduleTaskPoll(1000, true);
});

async function api(path, options = {}) {
    const response = await fetch(path, {
        headers: { "Content-Type": "application/json", ...(options.headers || {}) },
        ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(payload.error || payload.task?.error || `请求失败 (${response.status})`);
    }
    return payload;
}

function bindNavigation() {
    document.querySelectorAll("[data-view-target]").forEach((button) => {
        button.addEventListener("click", () => showView(button.dataset.viewTarget));
    });
    document.querySelectorAll("[data-view-link]").forEach((button) => {
        button.addEventListener("click", () => showView(button.dataset.viewLink));
    });
    document.querySelectorAll("[data-nav-collapse]").forEach((button) => {
        button.addEventListener("click", () => toggleNavGroup(button.dataset.navCollapse));
    });
}

function showView(viewName) {
    if (viewName === "signals") viewName = "daily-custom";
    state.currentView = viewName;
    document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === viewName));
    document.querySelectorAll("[data-view-target]").forEach((button) => {
        button.classList.toggle("active", button.dataset.viewTarget === viewName);
    });
    syncStrategyNav(viewName);
    const [eyebrow, title] = viewMeta[viewName] || viewMeta.overview;
    document.getElementById("pageEyebrow").textContent = eyebrow;
    document.getElementById("pageTitle").textContent = title;
    location.hash = viewName;
    if (viewName === "daily-custom") {
        // 从首页指标卡进入时同步选中状态，让策略矩阵与表格结果始终保持一致。
        renderIndicatorFilters();
        loadSignals();
    }
    if (viewName === "minute-custom") renderWatchlist(state.dashboard?.watchlist || []);
    if (viewName === "stocks") loadStocks();
    if (viewName === "etfs") loadEtfs();
    if (viewName === "runs") {
        loadRuns();
        scheduleTaskPoll(2500);
    } else if (!hasRunningTasks()) {
        cancelTaskPoll();
    }
    if (viewName === "overview") scheduleIndexPoll(1000);
    else cancelIndexPoll();
    if (viewName === "settings") loadDatabaseSettings();
    window.scrollTo({ top: 0, behavior: "smooth" });
}

function toggleNavGroup(groupName, forceOpen = null) {
    const group = document.querySelector(`[data-nav-group="${groupName}"]`);
    if (!group) return;
    const toggle = group.querySelector("[data-nav-collapse]");
    const items = group.querySelector(".nav-subitems");
    const expanded = forceOpen ?? toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(expanded));
    group.classList.toggle("expanded", expanded);
    if (items) items.hidden = !expanded;
}

function syncStrategyNav(viewName) {
    if (viewName.startsWith("daily-")) toggleNavGroup("daily", true);
    if (viewName.startsWith("minute-")) toggleNavGroup("minute", true);
}

function bindTheme() {
    const button = document.getElementById("themeToggle");
    updateThemeButton();
    button.addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        // 同步 Tabler 的颜色模式，使表格、表单和基础组件跟随项目主题切换。
        document.documentElement.dataset.bsTheme = next;
        localStorage.setItem("asm-theme", next);
        updateThemeButton();
        drawChart(state.chartPoints, state.chartItem);
        drawIndexChart(state.indexChartPoints, state.indexChartItem);
    });
}

function updateThemeButton() {
    const isDark = document.documentElement.dataset.theme === "dark";
    document.querySelector(".theme-icon").textContent = isDark ? "☾" : "☀";
    document.querySelector(".theme-label").textContent = isDark ? "深色" : "浅色";
}

function bindActions() {
    document.getElementById("reloadButton").addEventListener("click", loadDashboard);
    document.getElementById("startScanButton").addEventListener("click", () => startTask("scan"));
    document.getElementById("refreshStocksButton").addEventListener("click", () => startTask("refresh-stocks"));
    document.getElementById("reloadRuns").addEventListener("click", loadRuns);
    document.querySelectorAll('[data-action="scan"]').forEach((button) => {
        button.addEventListener("click", () => startTask("scan"));
    });
    document.querySelectorAll('[data-action="scan-current"]').forEach((button) => {
        button.addEventListener("click", () => startTask("scan", state.signalAssetType));
    });
    document.querySelectorAll('[data-action="refresh-stocks"]').forEach((button) => {
        button.addEventListener("click", () => startTask("refresh-stocks"));
    });
    document.querySelectorAll('[data-action="refresh-etfs"]').forEach((button) => {
        button.addEventListener("click", () => startTask("refresh-etfs"));
    });
    document.querySelectorAll("[data-error-modal-close]").forEach((button) => {
        button.addEventListener("click", closeErrorModal);
    });
    document.querySelectorAll("[data-group-modal-close]").forEach((button) => {
        button.addEventListener("click", closeGroupModal);
    });
    document.querySelectorAll("[data-assign-modal-close]").forEach((button) => {
        button.addEventListener("click", closeAssignGroupModal);
    });
    document.querySelectorAll("[data-confirm-modal-close]").forEach((button) => {
        button.addEventListener("click", () => resolveConfirmation(false));
    });
    document.querySelectorAll("[data-daily-chart-close]").forEach((button) => {
        button.addEventListener("click", closeDailyChartModal);
    });
    document.getElementById("confirmModalAccept")?.addEventListener("click", () => resolveConfirmation(true));
    document.querySelectorAll("[data-create-group]").forEach((button) => {
        button.addEventListener("click", () => openGroupModal(button.dataset.createGroup));
    });
    document.getElementById("groupForm")?.addEventListener("submit", createGroup);
    document.getElementById("databaseSettingsForm")?.addEventListener("submit", saveDatabaseSettings);
    document.getElementById("deleteStockGroup")?.addEventListener("click", () => deleteCurrentGroup("stock"));
    document.getElementById("deleteEtfGroup")?.addEventListener("click", () => deleteCurrentGroup("etf"));
    document.getElementById("retryErrorsButton")?.addEventListener("click", retrySelectedErrors);
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const assignModal = document.getElementById("assignGroupModal");
        const groupModal = document.getElementById("groupModal");
        const errorModal = document.getElementById("errorModal");
        const confirmModal = document.getElementById("confirmModal");
        const dailyChartModal = document.getElementById("dailyChartModal");
        if (confirmModal && !confirmModal.hidden) resolveConfirmation(false);
        else if (dailyChartModal && !dailyChartModal.hidden) closeDailyChartModal();
        else if (assignModal && !assignModal.hidden) closeAssignGroupModal();
        else if (groupModal && !groupModal.hidden) closeGroupModal();
        else if (errorModal && !errorModal.hidden) closeErrorModal();
    });
}

function bindFilters() {
    document.getElementById("signalSearch").addEventListener("input", debounce(loadSignals, 280));
    document.querySelectorAll("[data-signal-asset]").forEach((button) => {
        button.addEventListener("click", () => selectSignalAssetType(button.dataset.signalAsset));
    });
    document.getElementById("stockSearch").addEventListener("input", debounce(() => {
        state.stockPage = 1;
        loadStocks();
    }, 280));
    document.getElementById("marketFilter").addEventListener("change", () => {
        state.stockPage = 1;
        loadStocks();
    });
    document.getElementById("stockPrev").addEventListener("click", () => {
        if (state.stockPage > 1) {
            state.stockPage -= 1;
            loadStocks();
        }
    });
    document.getElementById("stockNext").addEventListener("click", () => {
        if (state.stockPage * state.stockPageSize < state.stockTotal) {
            state.stockPage += 1;
            loadStocks();
        }
    });
    document.getElementById("etfSearch")?.addEventListener("input", debounce(() => {
        state.etfPage = 1;
        loadEtfs();
    }, 280));
    document.getElementById("etfMarketFilter")?.addEventListener("change", () => {
        state.etfPage = 1;
        loadEtfs();
    });
    document.getElementById("etfPrev")?.addEventListener("click", () => {
        if (state.etfPage > 1) {
            state.etfPage -= 1;
            loadEtfs();
        }
    });
    document.getElementById("etfNext")?.addEventListener("click", () => {
        if (state.etfPage * state.etfPageSize < state.etfTotal) {
            state.etfPage += 1;
            loadEtfs();
        }
    });
}

async function loadDashboard() {
    const reloadButton = document.getElementById("reloadButton");
    reloadButton.classList.add("spinning");
    try {
        const data = await api("/api/dashboard");
        state.dashboard = data;
        state.indicators = data.indicators || [];
        renderMarket(data.market);
        renderMetrics(data);
        renderIndicators(data.indicators || []);
        renderLatestSignals(data.latest_signals || []);
        renderWatchlist(data.watchlist || []);
        renderSources(data.sources || []);
        renderTasks(data.tasks || {}, data.latest_scan);
        renderIndicatorFilters();
        const indexItems = data.indices?.items || [];
        renderIndexTabs(indexItems);
        if (needsIndexRefresh(indexItems)) loadIndexTrends(true);
        document.getElementById("syncCopy").textContent = `更新于 ${formatTime(data.generated_at)}`;
    } catch (error) {
        toast(error.message, true);
        document.getElementById("syncCopy").textContent = "读取失败";
    } finally {
        reloadButton.classList.remove("spinning");
    }
}

function renderMarket(market) {
    state.marketOpen = Boolean(market.is_open);
    const badge = document.querySelector(".market-badge");
    badge.classList.toggle("closed", !market.is_open);
    document.getElementById("marketLabel").textContent = `${market.label} · ${market.time}`;
    scheduleIndexPoll();
}

function renderMetrics(data) {
    const stats = data.stats || {};
    const scan = data.latest_scan || {};
    const totalSignals = (data.indicators || []).reduce((total, item) => total + item.count, 0);
    const metrics = [
        ["股票主数据", stats.stock_count || 0, `另有 ${formatNumber(stats.etf_count || 0)} 只ETF`, "◎", "var(--green)"],
        ["本轮命中标的", scan.matched_stocks || 0, scan.status ? statusLabel(scan.status) : "尚未执行扫描", "◇", "var(--cyan)"],
        ["策略信号记录", totalSignals, `${(data.indicators || []).length} 个可用指标`, "⌁", "var(--amber)"],
        ["历史K线缓存", stats.kline_count || 0, `${stats.cached_symbol_count || 0} 个证券已有缓存`, "▥", "var(--violet)"],
    ];
    document.getElementById("metricGrid").innerHTML = metrics.map(([label, value, foot, icon, color]) => `
        <article class="card metric-card" style="--metric-color:${color}">
            <div class="metric-top"><span>${escapeHtml(label)}</span><i class="metric-icon">${icon}</i></div>
            <strong class="metric-value">${formatNumber(value)}</strong><span class="metric-foot">${escapeHtml(foot)}</span>
        </article>
    `).join("");
}

function renderIndicators(indicators) {
    const container = document.getElementById("indicatorGrid");
    if (!indicators.length) {
        container.innerHTML = '<div class="empty-state">暂无策略指标</div>';
        return;
    }
    const sorted = [...indicators].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
    container.innerHTML = sorted.slice(0, 10).map((item) => indicatorCard(item)).join("");
    container.querySelectorAll(".indicator-card").forEach((button) => {
        button.addEventListener("click", () => {
            state.selectedSignal = button.dataset.signal;
            showView("daily-custom");
        });
    });
}

function indicatorCard(item) {
    const tone = toneColors[item.tone] || "var(--green)";
    return `
        <button class="indicator-card" data-signal="${escapeHtml(item.label)}" type="button" style="--tone:${tone}">
            <i class="indicator-dot"></i><span class="indicator-name" title="${escapeHtml(item.label)}">${escapeHtml(item.label)}</span>
            <strong class="indicator-count">${formatNumber(item.count)}</strong>
        </button>
    `;
}

function renderLatestSignals(items) {
    const rows = document.getElementById("latestSignalRows");
    if (!items.length) {
        rows.innerHTML = '<tr><td colspan="3" class="table-empty">完成首次扫描后，这里会显示最新命中标的</td></tr>';
        return;
    }
    rows.innerHTML = items.map((item) => `
        <tr><td><div class="stock-cell"><strong>${escapeHtml(item.stock_name)}</strong><span>${escapeHtml(item.ts_code)}</span></div></td>
        <td><span class="signal-tag">${escapeHtml(item.signal_type)}</span></td><td>#${item.run_id}</td></tr>
    `).join("");
}

async function loadIndexTrends(refreshMissing = false) {
    const container = document.getElementById("indexTabs");
    if (!container) return;
    try {
        if (refreshMissing) document.getElementById("indexChartMeta").textContent = "正在补齐指数日线缓存…";
        const refreshFlag = refreshMissing ? "&refresh=1" : "";
        const data = await api(`/api/indices?limit=120${refreshFlag}`);
        state.indexItems = data.items || [];
        renderIndexTabs(state.indexItems);
    } catch (error) {
        container.innerHTML = '<div class="empty-state">指数走势读取失败</div>';
        state.indexChartPoints = [];
        drawIndexChart([], null);
    }
}

function needsIndexRefresh(items) {
    return !items.length || items.some((item) => item.needs_refresh || !Array.isArray(item.points) || !item.points.length);
}

function renderIndexTabs(items) {
    const container = document.getElementById("indexTabs");
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">暂无指数数据</div>';
        drawIndexChart([], null);
        return;
    }
    container.innerHTML = items.map((item, index) => {
        const change = numberOrNull(item.pct_chg);
        const range = numberOrNull(item.range_pct);
        const changeClass = change === null ? "" : change >= 0 ? "positive" : "negative";
        return `
            <button class="index-tab ${index === 0 ? "active" : ""}" data-index="${index}" type="button">
                <span><strong>${escapeHtml(item.short_name || item.name)}</strong><small>${escapeHtml(item.symbol)}</small></span>
                <b>${item.close == null ? "—" : Number(item.close).toFixed(2)}</b>
                <em class="${changeClass}">${change === null ? "等待数据" : formatPercent(change)}</em>
                <small>区间 ${range === null ? "—" : formatPercent(range)}</small>
            </button>
        `;
    }).join("");
    container.querySelectorAll(".index-tab").forEach((button) => {
        button.addEventListener("click", () => selectIndexItem(items, Number(button.dataset.index), button));
    });
    selectIndexItem(items, 0, container.querySelector(".index-tab"));
}

function selectIndexItem(items, index, button) {
    document.querySelectorAll(".index-tab").forEach((item) => item.classList.remove("active"));
    button?.classList.add("active");
    const item = items[index];
    state.indexChartRequestId += 1;
    state.indexHistoryLoading = false;
    state.indexHistoryExhausted = false;
    state.indexChartItem = item;
    state.indexChartPoints = item.points || [];
    document.getElementById("indexChartTitle").textContent = `${item.name} · ${item.symbol}`;
    const source = sourceDisplayName(item.source);
    document.getElementById("indexChartMeta").textContent = item.trade_time ? `日线 · ${formatListDate(item.trade_time)} · ${source}` : "等待指数数据";
    drawIndexChart(state.indexChartPoints, item);
}

function renderWatchlist(items) {
    const container = document.getElementById("watchlist");
    if (!items.length) {
        container.innerHTML = '<div class="empty-state">暂无ETF监控标的</div>';
        return;
    }
    container.innerHTML = items.map((item, index) => {
        const change = numberOrNull(item.pct_chg);
        const changeClass = change === null ? "" : change >= 0 ? "positive" : "negative";
        return `
            <button class="watch-item ${index === 0 ? "active" : ""}" data-index="${index}" type="button">
                <span class="watch-name"><strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.code)}</span></span>
                <span class="watch-price"><b>${item.close == null ? "—" : Number(item.close).toFixed(3)}</b>
                <small class="${changeClass}">${change === null ? "等待采集" : formatPercent(change)}</small></span>
            </button>
        `;
    }).join("");
    container.querySelectorAll(".watch-item").forEach((button) => {
        button.addEventListener("click", () => selectWatchItem(items, Number(button.dataset.index), button));
    });
    selectWatchItem(items, 0, container.querySelector(".watch-item"));
}

function selectWatchItem(items, index, button) {
    document.querySelectorAll(".watch-item").forEach((item) => item.classList.remove("active"));
    button?.classList.add("active");
    const item = items[index];
    document.getElementById("chartTitle").textContent = `${item.name} · ${item.code}`;
    document.getElementById("chartMeta").textContent = item.trade_time ? `${item.period} · ${formatDate(item.trade_time)}` : "等待K线缓存";
    loadKlines(item);
}

async function loadKlines(item) {
    try {
        const period = item.period || "15min";
        let data = await api(`/api/klines/${encodeURIComponent(item.symbol)}?period=${period}&limit=120`);
        if (!data.items.length && period !== "D") {
            data = await api(`/api/klines/${encodeURIComponent(item.symbol)}?period=D&limit=120`);
        }
        state.chartPoints = data.items;
        state.chartItem = item;
        drawChart(data.items, item);
    } catch (error) {
        state.chartPoints = [];
        drawChart([], item);
    }
}

function drawChart(points, item) {
    const canvas = document.getElementById("priceChart");
    drawLineChart(canvas, points, item, {
        emptyText: item ? "暂无本地K线，运行监控后将显示走势" : "选择ETF查看走势",
        colorVariable: "--green",
    });
}

function drawIndexChart(points, item) {
    const container = document.getElementById("indexTrendChart");
    const library = window.LightweightCharts;
    const candles = normalizeDailyCandles(points);
    destroyIndexChart();
    if (!library || candles.length < 2) {
        const emptyText = item ? "暂无指数K线，稍后刷新重试" : "正在加载主要指数走势";
        container.innerHTML = `<div class="chart-loading">${emptyText}</div>`;
        return;
    }
    container.innerHTML = "";
    const styles = getComputedStyle(document.documentElement);
    const red = styles.getPropertyValue("--red").trim();
    const green = styles.getPropertyValue("--green").trim();
    const amber = styles.getPropertyValue("--amber").trim();
    const cyan = styles.getPropertyValue("--cyan").trim();
    const blue = styles.getPropertyValue("--blue").trim();
    const violet = styles.getPropertyValue("--violet").trim();
    const chart = library.createChart(container, {
        ...dailyChartThemeOptions(), autoSize: true,
        handleScale: { axisPressedMouseMove: true, mouseWheel: false, pinch: true },
        rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.12 } },
        timeScale: {
            borderVisible: false, timeVisible: false, rightOffset: 1, barSpacing: 7, minBarSpacing: 2,
            fixRightEdge: true, rightBarStaysOnScroll: true,
        },
    });
    const series = chart.addSeries(library.CandlestickSeries, {
        upColor: red, downColor: green, wickUpColor: red, wickDownColor: green,
        borderVisible: false, priceLineVisible: false,
    });
    const ma5Series = chart.addSeries(library.LineSeries, indexMovingAverageOptions(amber));
    const ma10Series = chart.addSeries(library.LineSeries, indexMovingAverageOptions(cyan));
    const ma20Series = chart.addSeries(library.LineSeries, indexMovingAverageOptions(blue));
    const ma60Series = chart.addSeries(library.LineSeries, indexMovingAverageOptions(violet));
    series.setData(candles);
    ma5Series.setData(movingAverageSeries(candles, 5));
    ma10Series.setData(movingAverageSeries(candles, 10));
    ma20Series.setData(movingAverageSeries(candles, 20));
    ma60Series.setData(movingAverageSeries(candles, 60));
    chart.timeScale().fitContent();
    state.indexChart = chart;
    bindIndexRightAnchoredZoom(container, chart);
    subscribeIndexHistoryLoading(chart, { series, ma5Series, ma10Series, ma20Series, ma60Series }, item);
}

function bindIndexRightAnchoredZoom(container, chart) {
    const handler = (event) => {
        if (!event.deltaY || state.indexChart !== chart) return;
        event.preventDefault();
        state.indexChartWheelDelta += event.deltaY;
        window.clearTimeout(state.indexChartWheelTimer);
        // 触控板的一次手势会产生大量wheel事件，合并后只执行一次8%的缩放。
        state.indexChartWheelTimer = window.setTimeout(() => {
            const currentRange = chart.timeScale().getVisibleLogicalRange();
            const wheelDelta = state.indexChartWheelDelta;
            state.indexChartWheelDelta = 0;
            state.indexChartWheelTimer = null;
            if (!currentRange || !wheelDelta || state.indexChart !== chart) return;
            const currentWidth = Math.max(1, currentRange.to - currentRange.from);
            const zoomFactor = 1 + INDEX_WHEEL_ZOOM_STEP;
            const requestedWidth = wheelDelta > 0 ? currentWidth * zoomFactor : currentWidth / zoomFactor;
            const nextWidth = Math.min(INDEX_MAX_VISIBLE_BARS, Math.max(INDEX_MIN_VISIBLE_BARS, requestedWidth));
            chart.timeScale().setVisibleLogicalRange({ from: currentRange.to - nextWidth, to: currentRange.to });
        }, INDEX_WHEEL_GESTURE_DELAY);
    };
    state.indexChartWheelHandler = handler;
    container.addEventListener("wheel", handler, { capture: true, passive: false });
}

function subscribeIndexHistoryLoading(chart, chartSeries, item) {
    chart.timeScale().subscribeVisibleLogicalRangeChange((logicalRange) => {
        if (!logicalRange || logicalRange.from > 12 || state.indexChart !== chart) return;
        loadOlderIndexHistory(chart, chartSeries, item);
    });
}

async function loadOlderIndexHistory(chart, chartSeries, item) {
    if (state.indexHistoryLoading || state.indexHistoryExhausted || state.indexChart !== chart) return;
    const currentCandles = normalizeDailyCandles(state.indexChartPoints);
    if (!currentCandles.length) return;
    const requestId = state.indexChartRequestId;
    const earliestDate = currentCandles[0].time;
    let canContinueLoading = false;
    state.indexHistoryLoading = true;
    updateIndexChartMeta(item, "正在加载更早日线…");
    try {
        const path = `/api/indices/${encodeURIComponent(item.symbol)}/history?before=${encodeURIComponent(earliestDate)}&limit=120&ensure=1`;
        const data = await api(path);
        if (requestId !== state.indexChartRequestId || state.indexChart !== chart) return;
        const olderPoints = data.items || [];
        state.indexHistoryExhausted = !data.has_more || !olderPoints.length;
        if (!olderPoints.length) {
            updateIndexChartMeta(item, "已到最早可用日线");
            return;
        }
        const visibleRange = chart.timeScale().getVisibleLogicalRange();
        const previousLength = currentCandles.length;
        state.indexChartPoints = mergeDailyPoints(olderPoints, state.indexChartPoints);
        item.points = state.indexChartPoints;
        const candles = normalizeDailyCandles(state.indexChartPoints);
        const addedCount = candles.length - previousLength;
        if (addedCount <= 0) {
            state.indexHistoryExhausted = true;
            updateIndexChartMeta(item, "已到最早可用日线");
            return;
        }
        chartSeries.series.setData(candles);
        chartSeries.ma5Series.setData(movingAverageSeries(candles, 5));
        chartSeries.ma10Series.setData(movingAverageSeries(candles, 10));
        chartSeries.ma20Series.setData(movingAverageSeries(candles, 20));
        chartSeries.ma60Series.setData(movingAverageSeries(candles, 60));
        if (visibleRange && addedCount > 0) {
            // 前插历史后逻辑索引整体右移，补偿索引可确保原来的右侧日线停留在原位置。
            chart.timeScale().setVisibleLogicalRange({ from: visibleRange.from + addedCount, to: visibleRange.to + addedCount });
        }
        canContinueLoading = Boolean(data.has_more);
        updateIndexChartMeta(item, `已加载 ${candles.length} 个交易日`);
    } catch (error) {
        updateIndexChartMeta(item, "历史日线加载失败，可再次滚动重试");
    } finally {
        if (requestId === state.indexChartRequestId) {
            state.indexHistoryLoading = false;
            if (canContinueLoading && !state.indexHistoryExhausted) {
                window.setTimeout(() => continueIndexHistoryLoading(chart, chartSeries, item), 0);
            }
        }
    }
}

function continueIndexHistoryLoading(chart, chartSeries, item) {
    const visibleRange = chart.timeScale().getVisibleLogicalRange();
    if (visibleRange && visibleRange.from <= 12) loadOlderIndexHistory(chart, chartSeries, item);
}

function mergeDailyPoints(olderPoints, currentPoints) {
    const points = new Map();
    [...olderPoints, ...currentPoints].forEach((point) => {
        const date = String(point.trade_time || point.time || "").slice(0, 10);
        if (date) points.set(date, point);
    });
    return [...points.values()].sort((left, right) => String(left.trade_time).localeCompare(String(right.trade_time)));
}

function updateIndexChartMeta(item, suffix) {
    if (state.indexChartItem !== item) return;
    const source = sourceDisplayName(item.source);
    const base = item.trade_time ? `日线 · ${formatListDate(item.trade_time)} · ${source}` : "等待指数数据";
    document.getElementById("indexChartMeta").textContent = suffix ? `${base} · ${suffix}` : base;
}

function indexMovingAverageOptions(color) {
    return { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
}

function destroyIndexChart() {
    const container = document.getElementById("indexTrendChart");
    window.clearTimeout(state.indexChartWheelTimer);
    state.indexChartWheelTimer = null;
    state.indexChartWheelDelta = 0;
    if (container && state.indexChartWheelHandler) {
        container.removeEventListener("wheel", state.indexChartWheelHandler, { capture: true });
    }
    state.indexChartWheelHandler = null;
    if (state.indexChart) state.indexChart.remove();
    state.indexChart = null;
}

function cancelIndexPoll() {
    window.clearTimeout(state.indexPollTimer);
    state.indexPollTimer = null;
}

function scheduleIndexPoll(delay = 120000) {
    cancelIndexPoll();
    if (!state.marketOpen || state.currentView !== "overview" || document.hidden) return;
    state.indexPollTimer = window.setTimeout(refreshIndexDuringTrading, delay);
}

async function refreshIndexDuringTrading() {
    if (!state.marketOpen || state.currentView !== "overview" || document.hidden) return;
    await loadIndexTrends(true);
    scheduleIndexPoll();
}

function drawLineChart(canvas, points, item, options = {}) {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
    const context = canvas.getContext("2d");
    context.scale(ratio, ratio);
    const width = rect.width;
    const height = rect.height;
    const styles = getComputedStyle(document.documentElement);
    const muted = styles.getPropertyValue("--text-muted").trim();
    const lineColor = styles.getPropertyValue(options.colorVariable || "--green").trim();
    const border = styles.getPropertyValue("--border").trim();
    const text = styles.getPropertyValue("--text").trim();
    context.clearRect(0, 0, width, height);
    context.strokeStyle = border;
    context.lineWidth = 1;
    for (let row = 1; row < 4; row += 1) {
        const y = (height / 4) * row;
        context.beginPath();
        context.moveTo(0, y);
        context.lineTo(width, y);
        context.stroke();
    }
    const values = points.map((point) => Number(point.close)).filter(Number.isFinite);
    if (values.length < 2) {
        context.fillStyle = muted;
        context.font = '13px "PingFang SC", sans-serif';
        context.textAlign = "center";
        context.fillText(options.emptyText || "暂无走势数据", width / 2, height / 2);
        return;
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const padding = 9;
    const coordinates = values.map((value, index) => ({
        x: padding + (index / (values.length - 1)) * (width - padding * 2),
        y: padding + (1 - (value - min) / range) * (height - padding * 2),
    }));
    const gradient = context.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, colorWithAlpha(lineColor, 0.24));
    gradient.addColorStop(1, colorWithAlpha(lineColor, 0));
    context.beginPath();
    context.moveTo(coordinates[0].x, height - padding);
    coordinates.forEach((point) => context.lineTo(point.x, point.y));
    context.lineTo(coordinates.at(-1).x, height - padding);
    context.closePath();
    context.fillStyle = gradient;
    context.fill();
    context.beginPath();
    coordinates.forEach((point, index) => index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y));
    context.strokeStyle = lineColor;
    context.lineWidth = 2;
    context.stroke();
    const latest = coordinates.at(-1);
    context.beginPath();
    context.arc(latest.x, latest.y, 3.5, 0, Math.PI * 2);
    context.fillStyle = lineColor;
    context.fill();
    if (options.showLatestLabel) {
        const latestValue = values.at(-1).toFixed(2);
        context.fillStyle = text;
        context.font = '12px "PingFang SC", sans-serif';
        context.textAlign = "right";
        context.fillText(latestValue, Math.min(width - 4, latest.x + 34), Math.max(16, latest.y - 8));
    }
}

function colorWithAlpha(color, alpha) {
    const context = document.createElement("canvas").getContext("2d");
    context.fillStyle = color;
    const normalized = context.fillStyle;
    if (normalized.startsWith("#") && normalized.length === 7) {
        const r = parseInt(normalized.slice(1, 3), 16);
        const g = parseInt(normalized.slice(3, 5), 16);
        const b = parseInt(normalized.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
    return color;
}

function renderSources(items) {
    const configured = ["baostock", "sina", "tencent", "eastmoney", "tushare", "yahoo", "sge", "sqlite"];
    const sourceMap = new Map(items.map((item) => [item.source.toLowerCase(), item]));
    const merged = configured.map((name) => sourceMap.get(name) || { source: name, dataset_count: 0, last_success_at: null });
    document.getElementById("sourceList").innerHTML = merged.map((item) => `
        <div class="source-item"><i class="source-state" style="opacity:${item.dataset_count ? 1 : 0.3}"></i>
        <div class="source-copy"><strong>${sourceDisplayName(item.source)}</strong>
        <span>${item.last_success_at ? `最近成功 ${formatDate(item.last_success_at)}` : "等待首次使用"}</span></div>
        <span class="source-count">${item.dataset_count || 0} 组数据</span></div>
    `).join("");
}

function renderIndicatorFilters() {
    const container = document.getElementById("indicatorFilters");
    const totalCount = state.indicators.reduce((total, item) => total + Number(item.count || 0), 0);
    const assetLabel = state.signalAssetType === "etf" ? "ETF" : "股票";
    const items = [{ label: "全部策略", count: totalCount, summary: `${state.indicators.length} 个${assetLabel}日线策略` }, ...state.indicators];
    container.innerHTML = items.map((item) => `
        <button class="strategy-filter-card ${state.selectedSignal === (item.key ? item.label : "") ? "active" : ""}"
        data-signal="${item.key ? escapeHtml(item.label) : ""}" type="button" aria-pressed="${state.selectedSignal === (item.key ? item.label : "")}"
        style="--strategy-tone:${toneColors[item.tone] || "var(--green)"}">
            <span class="strategy-filter-marker"></span>
            <span class="strategy-filter-copy"><strong>${escapeHtml(item.label)}</strong>
            <small>${escapeHtml(item.summary || `${assetLabel}日线自定义策略`)}</small></span>
            <span class="strategy-filter-count">${formatNumber(item.count || 0)}</span>
        </button>
    `).join("");
    container.querySelectorAll(".strategy-filter-card").forEach((button) => {
        button.addEventListener("click", () => {
            state.selectedSignal = button.dataset.signal;
            renderIndicatorFilters();
            loadSignals();
        });
    });
}

async function loadSignals() {
    const query = document.getElementById("signalSearch").value.trim();
    const params = new URLSearchParams({ limit: "500", q: query, asset_type: state.signalAssetType });
    if (state.selectedSignal) params.set("type", state.selectedSignal);
    try {
        const data = await api(`/api/signals?${params}`);
        const rows = document.getElementById("signalRows");
        state.indicators = data.indicators || [];
        renderIndicatorFilters();
        document.getElementById("signalResultCount").textContent = `${data.total} 条`;
        const assetLabel = state.signalAssetType === "etf" ? "ETF" : "股票";
        document.getElementById("signalTableTitle").textContent = state.selectedSignal || `全部${assetLabel}指标`;
        if (!data.items.length) {
            rows.innerHTML = `<tr><td colspan="5" class="table-empty">当前条件下暂无命中${assetLabel}</td></tr>`;
            return;
        }
        rows.innerHTML = data.items.map((item) => {
            const instrument = { ts_code: item.ts_code, name: item.stock_name };
            return `<tr ${instrumentPreviewAttributes(instrument)}><td><strong>${escapeHtml(item.ts_code)}</strong></td>
                <td>${instrumentNameCell(instrument)}</td><td><span class="signal-tag">${escapeHtml(item.signal_type)}</span></td>
                <td>${formatDate(item.created_at)}</td><td><button class="row-action daily-view-action" data-view-daily="${escapeHtml(item.ts_code)}"
                data-name="${escapeHtml(item.stock_name)}" type="button">查看日线</button></td></tr>`;
        }).join("");
        bindInstrumentActions(rows);
    } catch (error) {
        toast(error.message, true);
    }
}

function selectSignalAssetType(assetType) {
    if (!assetType || state.signalAssetType === assetType) return;
    state.signalAssetType = assetType;
    state.selectedSignal = "";
    document.querySelectorAll("[data-signal-asset]").forEach((button) => {
        const active = button.dataset.signalAsset === assetType;
        button.classList.toggle("active", active);
        button.setAttribute("aria-selected", String(active));
    });
    loadSignals();
}

async function loadStocks() {
    const query = document.getElementById("stockSearch").value.trim();
    const market = document.getElementById("marketFilter").value;
    const params = new URLSearchParams({
        page: String(state.stockPage),
        page_size: String(state.stockPageSize),
        q: query,
        market,
    });
    if (state.stockGroupId) params.set("group_id", String(state.stockGroupId));
    try {
        const data = await api(`/api/stocks?${params}`);
        state.stockTotal = data.total;
        state.stockGroups = data.groups || [];
        const supportsGroups = Boolean(document.getElementById("stockGroupTabs"));
        if (supportsGroups) renderGroupTabs("stock");
        const rows = document.getElementById("stockRows");
        document.getElementById("stockResultCount").textContent = `${formatNumber(data.total)} 只`;
        document.getElementById("stockPageLabel").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
        document.getElementById("stockPrev").disabled = data.page <= 1;
        document.getElementById("stockNext").disabled = data.page * data.page_size >= data.total;
        if (supportsGroups) {
            rows.innerHTML = data.items.length ? data.items.map((item) => `
                <tr ${instrumentPreviewAttributes(item)}><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${instrumentNameCell(item)}</td>
                <td>${escapeHtml(item.market || "—")}</td><td>${formatListDate(item.list_date)}</td>
                <td>${sourceDisplayName(item.source)}</td><td>${instrumentActions("stock", item)}</td></tr>
            `).join("") : '<tr><td colspan="6" class="table-empty">当前列表没有匹配的股票</td></tr>';
            bindInstrumentActions(rows);
        } else {
            rows.innerHTML = data.items.length ? data.items.map((item) => `
                <tr ${instrumentPreviewAttributes(item)}><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${instrumentNameCell(item)}</td>
                <td>${escapeHtml(item.market || "—")}</td><td>${formatListDate(item.list_date)}</td>
                <td>${sourceDisplayName(item.source)}</td></tr>
            `).join("") : '<tr><td colspan="5" class="table-empty">当前列表没有匹配的股票</td></tr>';
            bindDailyPreviewRows(rows);
        }
    } catch (error) {
        toast(error.message, true);
    }
}

async function loadEtfs() {
    const query = document.getElementById("etfSearch").value.trim();
    const market = document.getElementById("etfMarketFilter").value;
    const params = new URLSearchParams({
        page: String(state.etfPage),
        page_size: String(state.etfPageSize),
        q: query,
        market,
    });
    if (state.etfGroupId) params.set("group_id", String(state.etfGroupId));
    try {
        const data = await api(`/api/etfs?${params}`);
        state.etfTotal = data.total;
        state.etfGroups = data.groups || [];
        renderGroupTabs("etf");
        const rows = document.getElementById("etfRows");
        document.getElementById("etfResultCount").textContent = `${formatNumber(data.total)} 只`;
        document.getElementById("etfPageLabel").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
        document.getElementById("etfPrev").disabled = data.page <= 1;
        document.getElementById("etfNext").disabled = data.page * data.page_size >= data.total;
        rows.innerHTML = data.items.length ? data.items.map((item) => `
            <tr ${instrumentPreviewAttributes(item)}><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${instrumentNameCell(item)}</td>
            <td>${escapeHtml(item.market || "—")}</td><td>${sourceDisplayName(item.source)}</td>
            <td>${instrumentActions("etf", item)}</td></tr>
        `).join("") : '<tr><td colspan="5" class="table-empty">ETF列表为空，请点击“更新ETF库”获取完整清单</td></tr>';
        bindInstrumentActions(rows);
    } catch (error) {
        toast(error.message, true);
    }
}

function renderGroupTabs(assetType) {
    const isStock = assetType === "stock";
    const groups = isStock ? state.stockGroups : state.etfGroups;
    const selectedGroupId = isStock ? state.stockGroupId : state.etfGroupId;
    const container = document.getElementById(isStock ? "stockGroupTabs" : "etfGroupTabs");
    const allTab = `<button class="group-tab ${selectedGroupId ? "" : "active"}" data-group-id="" role="tab"
        aria-selected="${selectedGroupId ? "false" : "true"}" type="button">全部</button>`;
    container.innerHTML = allTab + groups.map((group) => {
        const active = Number(selectedGroupId) === group.id ? "active" : "";
        const pinLabel = group.pinned_count ? ` · ${group.pinned_count}置顶` : "";
        return `<button class="group-tab ${active}" data-group-id="${group.id}" role="tab"
            aria-selected="${active ? "true" : "false"}" type="button">
            ${escapeHtml(group.name)}<span>${group.item_count}${pinLabel}</span></button>`;
    }).join("");
    container.querySelectorAll("[data-group-id]").forEach((button) => {
        button.addEventListener("click", () => selectInstrumentGroup(assetType, button.dataset.groupId));
    });
    const selected = groups.find((group) => group.id === Number(selectedGroupId));
    document.getElementById(isStock ? "stockTableTitle" : "etfTableTitle").textContent = selected?.name || (isStock ? "全部股票" : "全部ETF");
    const deleteButton = document.getElementById(isStock ? "deleteStockGroup" : "deleteEtfGroup");
    deleteButton.classList.toggle("is-placeholder", !selected);
    deleteButton.disabled = !selected;
    deleteButton.setAttribute("aria-hidden", selected ? "false" : "true");
}

function selectInstrumentGroup(assetType, groupId) {
    const selected = groupId ? Number(groupId) : null;
    if (assetType === "stock") {
        state.stockGroupId = selected;
        state.stockPage = 1;
        loadStocks();
    } else {
        state.etfGroupId = selected;
        state.etfPage = 1;
        loadEtfs();
    }
}

function instrumentActions(assetType, item) {
    const groupId = assetType === "stock" ? state.stockGroupId : state.etfGroupId;
    const code = escapeHtml(item.ts_code);
    const name = escapeHtml(item.name);
    const dailyButton = `<button class="row-action daily-view-action" data-view-daily="${code}" data-name="${name}" type="button">查看日线</button>`;
    if (!groupId) {
        return `<div class="row-actions">${dailyButton}<button class="row-action" data-assign-asset="${assetType}"
            data-code="${code}" data-name="${name}" type="button">加入分组</button></div>`;
    }
    const pinLabel = item.is_pinned ? "取消置顶" : "置顶";
    const pinClass = item.is_pinned ? "row-action pinned" : "row-action";
    return `<div class="row-actions">${dailyButton}<button class="${pinClass}" data-pin-asset="${assetType}" data-code="${code}"
        data-pinned="${item.is_pinned ? "true" : "false"}" type="button">${item.is_pinned ? "★" : "☆"} ${pinLabel}</button>
        <button class="row-action danger-action" data-remove-asset="${assetType}" data-code="${code}" type="button">移出分组</button></div>`;
}

function instrumentPreviewAttributes(item) {
    return `class="instrument-row" data-preview-symbol="${escapeHtml(item.ts_code)}" data-preview-name="${escapeHtml(item.name)}"`;
}

function instrumentNameCell(item) {
    return `<div class="instrument-name-cell"><strong>${escapeHtml(item.name)}</strong><small>悬停预览近3个月</small></div>`;
}

function bindInstrumentActions(container) {
    container.querySelectorAll("[data-view-daily]").forEach((button) => {
        button.addEventListener("click", () => openDailyChartModal(button.dataset.viewDaily, button.dataset.name));
    });
    container.querySelectorAll("[data-assign-asset]").forEach((button) => {
        button.addEventListener("click", () => openAssignGroupModal(button.dataset.assignAsset, button.dataset.code, button.dataset.name));
    });
    container.querySelectorAll("[data-pin-asset]").forEach((button) => {
        button.addEventListener("click", () => toggleInstrumentPin(button.dataset.pinAsset, button.dataset.code, button.dataset.pinned));
    });
    container.querySelectorAll("[data-remove-asset]").forEach((button) => {
        button.addEventListener("click", () => removeInstrumentFromGroup(button.dataset.removeAsset, button.dataset.code));
    });
    bindDailyPreviewRows(container);
}

function bindDailyPreviewRows(container) {
    container.querySelectorAll("[data-preview-symbol]").forEach((row) => {
        row.addEventListener("pointerenter", (event) => scheduleDailyPreview(row, event));
        row.addEventListener("pointermove", (event) => {
            if (event.pointerType === "touch") return;
            state.dailyPreviewPointer = { x: event.clientX, y: event.clientY };
            if (state.dailyPreviewAnchor === row) positionDailyPreview(event.clientX, event.clientY);
        });
        row.addEventListener("pointerleave", () => hideDailyPreview(row));
    });
}

function scheduleDailyPreview(row, event) {
    if (event.pointerType === "touch") return;
    window.clearTimeout(state.dailyPreviewTimer);
    state.dailyPreviewPointer = { x: event.clientX, y: event.clientY };
    state.dailyPreviewTimer = window.setTimeout(() => {
        showDailyPreview(row, row.dataset.previewSymbol, row.dataset.previewName);
    }, 260);
}

async function showDailyPreview(row, symbol, name) {
    const popover = document.getElementById("dailyPreviewPopover");
    state.dailyPreviewAnchor = row;
    document.getElementById("dailyPreviewName").textContent = name;
    document.getElementById("dailyPreviewSymbol").textContent = symbol;
    document.getElementById("dailyPreviewMeta").textContent = "正在读取日线缓存…";
    const changeElement = document.getElementById("dailyPreviewChange");
    changeElement.textContent = "—";
    changeElement.className = "";
    popover.hidden = false;
    positionDailyPreview(state.dailyPreviewPointer.x, state.dailyPreviewPointer.y);
    destroyDailyPreviewChart();
    document.getElementById("dailyPreviewChart").innerHTML = '<div class="chart-loading">正在加载近3个月日K…</div>';
    try {
        const data = await fetchDailySeries(symbol, 3, 90);
        if (state.dailyPreviewAnchor !== row) return;
        const points = data.items || [];
        renderDailyPreviewCandles(points, data.warning);
        const latest = points.at(-1) || {};
        const change = numberOrNull(latest.pct_chg);
        document.getElementById("dailyPreviewMeta").textContent = points.length
            ? `${String(latest.trade_time).slice(0, 10)} · ${sourceDisplayName(data.source)}`
            : "暂无日线缓存";
        changeElement.textContent = change === null ? "—" : formatPercent(change);
        changeElement.className = change === null ? "" : change >= 0 ? "positive" : "negative";
        positionDailyPreview(state.dailyPreviewPointer.x, state.dailyPreviewPointer.y);
    } catch (error) {
        if (state.dailyPreviewAnchor !== row) return;
        document.getElementById("dailyPreviewMeta").textContent = "日线读取失败";
        destroyDailyPreviewChart();
        document.getElementById("dailyPreviewChart").innerHTML = `<div class="chart-loading error">${escapeHtml(error.message)}</div>`;
    }
}

function positionDailyPreview(x, y) {
    const popover = document.getElementById("dailyPreviewPopover");
    if (!popover || popover.hidden) return;
    const gap = 16;
    const width = popover.offsetWidth;
    const height = popover.offsetHeight;
    const left = x + gap + width > window.innerWidth ? x - width - gap : x + gap;
    const top = y + gap + height > window.innerHeight ? y - height - gap : y + gap;
    popover.style.left = `${Math.max(12, Math.min(left, window.innerWidth - width - 12))}px`;
    popover.style.top = `${Math.max(12, Math.min(top, window.innerHeight - height - 12))}px`;
}

function hideDailyPreview(row = null) {
    window.clearTimeout(state.dailyPreviewTimer);
    if (row && state.dailyPreviewAnchor && state.dailyPreviewAnchor !== row) return;
    state.dailyPreviewAnchor = null;
    destroyDailyPreviewChart();
    const popover = document.getElementById("dailyPreviewPopover");
    if (popover) popover.hidden = true;
}

function renderDailyPreviewCandles(points, warning = "") {
    const container = document.getElementById("dailyPreviewChart");
    const library = window.LightweightCharts;
    const candles = normalizeDailyCandles(points);
    destroyDailyPreviewChart();
    if (!library || candles.length < 2) {
        container.innerHTML = `<div class="chart-loading">${escapeHtml(warning || "暂无足够的日K数据")}</div>`;
        return;
    }
    container.innerHTML = "";
    const styles = getComputedStyle(document.documentElement);
    const red = styles.getPropertyValue("--red").trim();
    const green = styles.getPropertyValue("--green").trim();
    const chart = library.createChart(container, {
        ...dailyChartThemeOptions(),
        autoSize: true,
        handleScroll: false,
        handleScale: false,
        rightPriceScale: { visible: false },
        timeScale: { visible: false, barSpacing: 5, minBarSpacing: 2 },
        crosshair: { vertLine: { visible: false }, horzLine: { visible: false } },
    });
    const series = chart.addSeries(library.CandlestickSeries, {
        upColor: red, downColor: green, wickUpColor: red, wickDownColor: green, borderVisible: false,
        priceLineVisible: false, lastValueVisible: false,
    });
    series.setData(candles);
    chart.timeScale().fitContent();
    state.dailyPreviewChart = chart;
}

function destroyDailyPreviewChart() {
    if (state.dailyPreviewChart) state.dailyPreviewChart.remove();
    state.dailyPreviewChart = null;
}

function applyDailyPreviewChartTheme() {
    if (state.dailyPreviewChart) state.dailyPreviewChart.applyOptions(dailyChartThemeOptions());
}

function fetchDailySeries(symbol, months, limit) {
    const key = `${symbol}:${months}:${limit}`;
    if (state.dailyChartCache.has(key)) return Promise.resolve(state.dailyChartCache.get(key));
    const pending = api(`/api/klines/${encodeURIComponent(symbol)}?period=D&months=${months}&limit=${limit}&ensure=1`);
    state.dailyChartCache.set(key, pending);
    return pending.then((data) => {
        state.dailyChartCache.set(key, data);
        return data;
    }).catch((error) => {
        state.dailyChartCache.delete(key);
        throw error;
    });
}

async function openDailyChartModal(symbol, name) {
    hideDailyPreview();
    const modal = document.getElementById("dailyChartModal");
    const requestId = ++state.dailyChartRequestId;
    destroyDailyChart();
    document.getElementById("dailyChartTitle").textContent = `${name} · ${symbol}`;
    document.getElementById("dailyChartSubtitle").textContent = "近一年日K、均线与成交量";
    document.getElementById("dailyChartContainer").innerHTML = '<div class="chart-loading">正在加载日线与成交量…</div>';
    document.getElementById("dailyChartWarning").hidden = true;
    renderDailyChartSummary([]);
    modal.hidden = false;
    syncModalOpenState();
    try {
        const data = await fetchDailySeries(symbol, 12, 260);
        if (requestId !== state.dailyChartRequestId || modal.hidden) return;
        const points = data.items || [];
        renderDailyChartSummary(points);
        const cacheState = data.has_ohlcv ? "OHLCV已缓存" : "OHLCV数据不完整";
        document.getElementById("dailyChartSource").textContent = `${sourceDisplayName(data.source)} · ${points.length} 个交易日 · ${cacheState}`;
        const warning = document.getElementById("dailyChartWarning");
        const warningMessage = data.warning || (data.has_ohlcv ? "" : "当前行情源未能提供完整的开高低收和成交量数据");
        warning.hidden = !warningMessage;
        warning.textContent = warningMessage;
        renderProfessionalDailyChart(points);
    } catch (error) {
        if (requestId !== state.dailyChartRequestId) return;
        document.getElementById("dailyChartContainer").innerHTML = `<div class="chart-loading error">${escapeHtml(error.message)}</div>`;
        document.getElementById("dailyChartSource").textContent = "日线读取失败";
    }
    modal.querySelector(".modal-close").focus();
}

function closeDailyChartModal() {
    const modal = document.getElementById("dailyChartModal");
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    state.dailyChartRequestId += 1;
    destroyDailyChart();
    syncModalOpenState();
}

function destroyDailyChart() {
    if (state.dailyChart) state.dailyChart.remove();
    state.dailyChart = null;
}

function renderDailyChartSummary(points) {
    const valid = points.filter((item) => numberOrNull(item.close) !== null);
    const latest = valid.at(-1) || {};
    const highs = valid.map((item) => numberOrNull(item.high)).filter((value) => value !== null);
    const lows = valid.map((item) => numberOrNull(item.low)).filter((value) => value !== null);
    const change = numberOrNull(latest.pct_chg);
    const values = [
        ["最新收盘", formatMarketPrice(latest.close)],
        ["涨跌幅", change === null ? "—" : formatPercent(change), change === null ? "" : change >= 0 ? "positive" : "negative"],
        ["区间最高", highs.length ? formatMarketPrice(Math.max(...highs)) : "—"],
        ["区间最低", lows.length ? formatMarketPrice(Math.min(...lows)) : "—"],
        ["最新成交量", formatCompactMarketValue(latest.vol)],
        ["最新成交额", formatCompactMarketValue(latest.amount, "元")],
    ];
    document.getElementById("dailyChartSummary").innerHTML = values.map(([label, value, className = ""]) => `
        <div><span>${label}</span><strong class="${className}">${value}</strong></div>
    `).join("");
}

function renderProfessionalDailyChart(points) {
    const container = document.getElementById("dailyChartContainer");
    const library = window.LightweightCharts;
    const candles = normalizeDailyCandles(points);
    if (!library || candles.length < 2) {
        container.innerHTML = `<div class="chart-loading">${library ? "暂无足够的日线数据" : "专业图表组件加载失败"}</div>`;
        return;
    }
    container.innerHTML = "";
    const styles = getComputedStyle(document.documentElement);
    const red = styles.getPropertyValue("--red").trim();
    const green = styles.getPropertyValue("--green").trim();
    const cyan = styles.getPropertyValue("--cyan").trim();
    const amber = styles.getPropertyValue("--amber").trim();
    const chart = library.createChart(container, { ...dailyChartThemeOptions(), autoSize: true });
    const candleSeries = chart.addSeries(library.CandlestickSeries, {
        upColor: red, downColor: green, wickUpColor: red, wickDownColor: green, borderVisible: false,
    });
    const ma5Series = chart.addSeries(library.LineSeries, { color: cyan, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const ma10Series = chart.addSeries(library.LineSeries, { color: amber, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    const volumeSeries = chart.addSeries(library.HistogramSeries, {
        priceFormat: { type: "volume" }, priceScaleId: "volume", priceLineVisible: false, lastValueVisible: false,
    });
    candleSeries.setData(candles);
    ma5Series.setData(movingAverageSeries(candles, 5));
    ma10Series.setData(movingAverageSeries(candles, 10));
    volumeSeries.setData(candles.map((item) => ({
        time: item.time, value: item.vol || 0, color: colorWithAlpha(item.close >= item.open ? red : green, 0.55),
    })));
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.28 } });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    chart.timeScale().fitContent();
    bindDailyChartCrosshair(chart, candleSeries, points, container);
    state.dailyChart = chart;
}

function dailyChartThemeOptions() {
    const styles = getComputedStyle(document.documentElement);
    const panel = styles.getPropertyValue("--panel-solid").trim();
    const text = styles.getPropertyValue("--text-soft").trim();
    const border = styles.getPropertyValue("--border").trim();
    return {
        layout: { background: { type: window.LightweightCharts?.ColorType.Solid, color: panel }, textColor: text, attributionLogo: false },
        grid: { vertLines: { color: border }, horzLines: { color: border } },
        rightPriceScale: { borderColor: border },
        timeScale: { borderColor: border, timeVisible: false, rightOffset: 2, barSpacing: 7, minBarSpacing: 2 },
        crosshair: { mode: window.LightweightCharts?.CrosshairMode.Normal },
        localization: { locale: "zh-CN" },
    };
}

function applyDailyChartTheme() {
    if (state.dailyChart) state.dailyChart.applyOptions(dailyChartThemeOptions());
}

function normalizeDailyCandles(points) {
    return points.map((item) => ({
        time: String(item.trade_time || "").slice(0, 10),
        open: numberOrNull(item.open),
        high: numberOrNull(item.high),
        low: numberOrNull(item.low),
        close: numberOrNull(item.close),
        vol: numberOrNull(item.vol),
    })).filter((item) => item.time && [item.open, item.high, item.low, item.close].every((value) => value !== null));
}

function movingAverageSeries(candles, windowSize) {
    const result = [];
    let total = 0;
    candles.forEach((item, index) => {
        total += item.close;
        if (index >= windowSize) total -= candles[index - windowSize].close;
        if (index >= windowSize - 1) result.push({ time: item.time, value: total / windowSize });
    });
    return result;
}

function bindDailyChartCrosshair(chart, candleSeries, points, container) {
    const detailByDate = new Map(points.map((item) => [String(item.trade_time || "").slice(0, 10), item]));
    const tooltip = document.createElement("div");
    tooltip.className = "daily-chart-tooltip";
    tooltip.hidden = true;
    container.appendChild(tooltip);
    chart.subscribeCrosshairMove((param) => {
        const candle = param.seriesData.get(candleSeries);
        const date = chartTimeKey(param.time);
        const detail = detailByDate.get(date);
        if (!candle || !detail || !param.point) {
            tooltip.hidden = true;
            return;
        }
        tooltip.innerHTML = `<strong>${date}</strong><span>开 ${formatMarketPrice(candle.open)} · 高 ${formatMarketPrice(candle.high)} ·
            低 ${formatMarketPrice(candle.low)} · 收 ${formatMarketPrice(candle.close)}</span>
            <span>量 ${formatCompactMarketValue(detail.vol)} · 额 ${formatCompactMarketValue(detail.amount, "元")}
            · 换手 ${numberOrNull(detail.turnover_rate) === null ? "—" : `${Number(detail.turnover_rate).toFixed(2)}%`}</span>`;
        tooltip.hidden = false;
    });
}

function chartTimeKey(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (typeof value === "object" && value.year) {
        return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}`;
    }
    return String(value).slice(0, 10);
}

function openGroupModal(assetType) {
    state.groupAssetType = assetType;
    const label = assetType === "stock" ? "股票" : "ETF";
    document.getElementById("groupModalTitle").textContent = `新建${label}分组`;
    document.getElementById("groupName").value = "";
    document.getElementById("groupModal").hidden = false;
    syncModalOpenState();
    document.getElementById("groupName").focus();
}

function closeGroupModal() {
    document.getElementById("groupModal").hidden = true;
    state.groupAssetType = null;
    syncModalOpenState();
}

async function createGroup(event) {
    event.preventDefault();
    const assetType = state.groupAssetType;
    const name = document.getElementById("groupName").value.trim();
    if (!assetType || !name) return;
    try {
        const data = await api("/api/instrument-groups", {
            method: "POST",
            body: JSON.stringify({ asset_type: assetType, name }),
        });
        closeGroupModal();
        toast(`分组“${data.group.name}”已创建`);
        if (assetType === "stock") {
            state.stockGroupId = data.group.id;
            state.stockPage = 1;
            await loadStocks();
        } else {
            state.etfGroupId = data.group.id;
            state.etfPage = 1;
            await loadEtfs();
        }
    } catch (error) {
        toast(error.message, true);
    }
}

async function deleteCurrentGroup(assetType) {
    const groupId = assetType === "stock" ? state.stockGroupId : state.etfGroupId;
    const groups = assetType === "stock" ? state.stockGroups : state.etfGroups;
    const group = groups.find((item) => item.id === Number(groupId));
    if (!group) return;
    const confirmed = await confirmAction({
        title: "删除自定义分组",
        message: `确定删除分组“${group.name}”吗？分组内条目不会从股票或ETF主列表删除。`,
        confirmText: "删除分组",
        tone: "danger",
    });
    if (!confirmed) return;
    try {
        await api(`/api/instrument-groups/${group.id}`, { method: "DELETE" });
        toast(`分组“${group.name}”已删除`);
        selectInstrumentGroup(assetType, "");
    } catch (error) {
        toast(error.message, true);
    }
}

function openAssignGroupModal(assetType, code, name) {
    const groups = assetType === "stock" ? state.stockGroups : state.etfGroups;
    if (!groups.length) {
        toast("请先创建一个自定义分组");
        openGroupModal(assetType);
        return;
    }
    state.assignment = { assetType, code, name };
    document.getElementById("assignInstrumentName").textContent = `${name} · ${code}`;
    document.getElementById("assignGroupOptions").innerHTML = groups.map((group) => `
        <button class="assignment-group" data-assign-group-id="${group.id}" type="button">
            <span><strong>${escapeHtml(group.name)}</strong><small>${group.item_count} 个条目</small></span><b>加入 →</b>
        </button>
    `).join("");
    document.querySelectorAll("[data-assign-group-id]").forEach((button) => {
        button.addEventListener("click", () => addInstrumentToGroup(Number(button.dataset.assignGroupId)));
    });
    document.getElementById("assignGroupModal").hidden = false;
    syncModalOpenState();
}

function closeAssignGroupModal() {
    document.getElementById("assignGroupModal").hidden = true;
    state.assignment = null;
    syncModalOpenState();
}

async function addInstrumentToGroup(groupId) {
    const assignment = state.assignment;
    if (!assignment) return;
    try {
        const data = await api(`/api/instrument-groups/${groupId}/items`, {
            method: "POST",
            body: JSON.stringify({ asset_code: assignment.code }),
        });
        toast(data.added ? `${assignment.name} 已加入分组` : `${assignment.name} 已经在该分组中`);
        const assetType = assignment.assetType;
        closeAssignGroupModal();
        await reloadInstrumentList(assetType);
    } catch (error) {
        toast(error.message, true);
    }
}

async function toggleInstrumentPin(assetType, code, pinnedText) {
    const groupId = assetType === "stock" ? state.stockGroupId : state.etfGroupId;
    if (!groupId) return;
    const pinned = pinnedText !== "true";
    try {
        await api(`/api/instrument-groups/${groupId}/items/${encodeURIComponent(code)}/pin`, {
            method: "PATCH",
            body: JSON.stringify({ pinned }),
        });
        toast(pinned ? "已在当前分组置顶" : "已取消置顶");
        await reloadInstrumentList(assetType);
    } catch (error) {
        toast(error.message, true);
    }
}

async function removeInstrumentFromGroup(assetType, code) {
    const groupId = assetType === "stock" ? state.stockGroupId : state.etfGroupId;
    if (!groupId) return;
    const confirmed = await confirmAction({
        title: "移出当前分组",
        message: "确定将该条目从当前分组移出吗？条目仍会保留在主列表和其他自定义分组中。",
        confirmText: "确认移出",
        tone: "danger",
    });
    if (!confirmed) return;
    try {
        await api(`/api/instrument-groups/${groupId}/items/${encodeURIComponent(code)}`, { method: "DELETE" });
        toast("已从当前分组移除");
        await reloadInstrumentList(assetType);
    } catch (error) {
        toast(error.message, true);
    }
}

function reloadInstrumentList(assetType) {
    return assetType === "stock" ? loadStocks() : loadEtfs();
}

function syncModalOpenState() {
    const hasOpenModal = [...document.querySelectorAll(".modal-shell")].some((modal) => !modal.hidden);
    document.body.classList.toggle("modal-open", hasOpenModal);
}

function confirmAction({ title, message, confirmText = "确认", tone = "primary" }) {
    const modal = document.getElementById("confirmModal");
    if (state.confirmationResolver) state.confirmationResolver(false);
    document.getElementById("confirmModalTitle").textContent = title;
    document.getElementById("confirmModalMessage").textContent = message;
    const acceptButton = document.getElementById("confirmModalAccept");
    acceptButton.textContent = confirmText;
    acceptButton.classList.toggle("confirm-danger-button", tone === "danger");
    document.getElementById("confirmModalIcon").classList.toggle("danger", tone === "danger");
    modal.hidden = false;
    syncModalOpenState();
    window.setTimeout(() => acceptButton.focus(), 0);
    return new Promise((resolve) => {
        state.confirmationResolver = resolve;
    });
}

function resolveConfirmation(confirmed) {
    const modal = document.getElementById("confirmModal");
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    syncModalOpenState();
    const resolver = state.confirmationResolver;
    state.confirmationResolver = null;
    resolver?.(confirmed);
}

async function loadRuns() {
    try {
        const data = await api("/api/task-progress?limit=20");
        renderTasks(data.tasks, data.latest_scan);
        renderRunRows(data.task_runs || data.scan_runs);
    } catch (error) {
        toast(error.message, true);
    }
}

async function loadDatabaseSettings() {
    const status = document.getElementById("databaseSettingsState");
    status.className = "status-pill running";
    status.textContent = "正在读取";
    try {
        const data = await api("/api/settings/database");
        state.databaseSettings = data;
        renderDatabaseSettings(data);
    } catch (error) {
        status.className = "status-pill failed";
        status.textContent = "读取失败";
        toast(error.message, true);
    }
}

function renderDatabaseSettings(data) {
    const managed = Boolean(data.managed_by_environment);
    const directoryInput = document.getElementById("databaseDirectory");
    const copyInput = document.getElementById("copyCurrentDatabase");
    const cloudModeInput = document.getElementById("cloudSyncMode");
    const saveButton = document.getElementById("saveDatabaseSettings");
    directoryInput.value = data.database_directory || "";
    cloudModeInput.checked = Boolean(data.cloud_sync_mode);
    document.getElementById("databaseCurrentPath").textContent = data.database_path || "—";
    document.getElementById("databaseSize").textContent = formatBytes(data.size_bytes);
    document.getElementById("databaseJournalMode").textContent = data.journal_mode || "—";
    document.getElementById("databaseSettingsState").className = "status-pill completed";
    document.getElementById("databaseSettingsState").textContent = managed ? "环境变量托管" : "已启用";
    [directoryInput, copyInput, cloudModeInput, saveButton].forEach((element) => {
        element.disabled = managed;
    });
    document.getElementById("databaseSettingsHint").textContent = managed
        ? "当前由 MARKET_DATA_DB 环境变量托管，需要在启动环境中修改。"
        : "切换期间不能有正在运行的扫描、刷新或重试任务。";
}

async function saveDatabaseSettings(event) {
    event.preventDefault();
    const directory = document.getElementById("databaseDirectory").value.trim();
    if (!directory) return;
    const confirmed = await confirmAction({
        title: "切换行情数据库",
        message: "切换后服务会立即使用新目录。使用OneDrive时，请等待文件同步完成后再在其他设备启动项目。",
        confirmText: "保存并切换",
        tone: "primary",
    });
    if (!confirmed) return;
    const button = document.getElementById("saveDatabaseSettings");
    button.disabled = true;
    button.textContent = "正在切换…";
    try {
        const data = await api("/api/settings/database", {
            method: "POST",
            body: JSON.stringify({
                database_directory: directory,
                copy_current: document.getElementById("copyCurrentDatabase").checked,
                cloud_sync_mode: document.getElementById("cloudSyncMode").checked,
            }),
        });
        state.databaseSettings = data;
        renderDatabaseSettings(data);
        if (data.copied_current_database) toast("当前数据库已安全复制，并切换到新目录");
        else if (data.used_existing_database) toast("已切换到目标目录中已有的数据库");
        else toast("数据库设置已保存");
        await loadDashboard();
    } catch (error) {
        toast(error.message, true);
    } finally {
        button.disabled = Boolean(state.databaseSettings?.managed_by_environment);
        button.textContent = "保存并切换";
    }
}

function renderRunRows(items) {
    const rows = document.getElementById("runRows");
    const supportsErrorModal = Boolean(document.getElementById("errorModal"));
    rows.innerHTML = items.length ? items.map((item) => {
        const progress = item.total_stocks ? Math.min(100, Math.round(item.processed_stocks / item.total_stocks * 100)) : 0;
        const isRunning = item.status === "running";
        const isPaused = item.status === "paused";
        const isActive = isRunning || isPaused;
        const hasError = item.error_count > 0 || Boolean(item.error_message);
        const errorLabel = item.error_count > 0 ? `${item.error_count} 条` : "查看原因";
        const isRetry = item.task_type === "error_retry";
        const scopeLabel = { stock: "股票", etf: "ETF", all: "全市场" }[item.scan_scope] || "全市场";
        const taskLabel = isRetry ? "错误标的重试" : `${scopeLabel}扫描`;
        const controlTask = isRetry ? "retry_errors" : "scan_market";
        const taskSource = isRetry
            ? item.parent_run_id ? `来源扫描任务 #${item.parent_run_id}` : "原扫描任务已删除"
            : item.scan_scope === "stock" ? "仅扫描股票" : item.scan_scope === "etf" ? "仅扫描ETF" : "全市场股票与ETF";
        const detailRunId = isRetry ? item.parent_run_id : item.id;
        const resultLabel = isRetry ? `${item.matched_stocks} 条已解决` : `${item.matched_stocks} 个标的命中`;
        let errorCell = `<span class="quiet-label">0</span>`;
        if (hasError && supportsErrorModal && detailRunId) {
            errorCell = `<button class="error-count-button has-error" data-error-run="${detailRunId}" type="button">${errorLabel}</button>`;
        } else if (hasError) {
            errorCell = `<span class="status-pill failed" title="${escapeHtml(item.error_message || "存在未解决错误")}">${errorLabel}</span>`;
        }
        const actionCell = isRunning
            ? `<button class="row-action" data-pause-task="${controlTask}" type="button">暂停</button>`
            : isPaused
                ? `<button class="row-action" data-resume-task="${controlTask}" type="button">继续</button>`
                : `<button class="row-action danger-action" data-delete-run="${item.id}" type="button">删除</button>`;
        return `<tr class="scan-row ${isActive ? "is-running" : ""}"><td>
            <div class="stock-cell"><strong>#${item.id} · ${taskLabel}</strong><span>${taskSource}</span></div></td>
            <td><span class="status-pill ${item.status}">${statusLabel(item.status)}</span></td>
            <td><div class="table-progress"><div class="progress-copy"><strong>${item.processed_stocks}/${item.total_stocks}</strong>
            <span>${progress}%</span></div><div class="mini-progress ${isRunning ? "running" : ""}">
            <span style="width:${progress}%"></span></div></div></td><td><strong>${resultLabel}</strong></td>
            <td>${errorCell}</td><td>${formatDate(item.started_at)}</td><td>${actionCell}</td></tr>`;
    }).join("") : '<tr><td colspan="7" class="table-empty">暂无任务记录</td></tr>';
    rows.querySelectorAll("[data-error-run]").forEach((button) => {
        button.addEventListener("click", () => openErrorModal(Number(button.dataset.errorRun)));
    });
    rows.querySelectorAll("[data-pause-task]").forEach((button) => {
        button.addEventListener("click", () => controlTask(button.dataset.pauseTask, "pause", button));
    });
    rows.querySelectorAll("[data-resume-task]").forEach((button) => {
        button.addEventListener("click", () => controlTask(button.dataset.resumeTask, "resume", button));
    });
    rows.querySelectorAll("[data-delete-run]").forEach((button) => {
        button.addEventListener("click", () => deleteTaskRun(Number(button.dataset.deleteRun), button));
    });
}

async function controlTask(taskName, action, button) {
    button.disabled = true;
    button.textContent = action === "pause" ? "暂停中…" : "继续中…";
    try {
        const data = await api(`/api/tasks/${taskName}/${action}`, { method: "POST", body: "{}" });
        state.tasks[taskName] = data.task;
        toast(action === "pause" ? "任务已暂停" : "任务已继续");
        await loadRuns();
        scheduleTaskPoll(0, true);
    } catch (error) {
        toast(error.message, true);
        await loadRuns();
    }
}

async function deleteTaskRun(runId, button) {
    const confirmed = await confirmAction({
        title: `删除任务 #${runId}`,
        message: "该任务关联的策略结果和错误明细也会一并删除，此操作无法撤销。",
        confirmText: "确认删除",
        tone: "danger",
    });
    if (!confirmed) return;
    button.disabled = true;
    button.textContent = "删除中…";
    try {
        await api(`/api/task-runs/${runId}`, { method: "DELETE" });
        toast(`任务 #${runId} 已删除`);
        await Promise.all([loadRuns(), loadDashboard()]);
    } catch (error) {
        toast(error.message, true);
        button.disabled = false;
        button.textContent = "删除";
    }
}

function renderTasks(tasks, latestScan) {
    state.tasks = tasks;
    const definitions = [
        ["refresh_stocks", "股票列表刷新", "更新SQLite中的证券主数据快照"],
        ["refresh_etfs", "ETF列表刷新", "更新SQLite中的ETF主数据快照"],
        ["scan_market", "全市场策略扫描", "逐只计算所有启用的策略指标"],
        ["retry_errors", "错误标的重试", "只重新处理扫描失败的标的列表"],
    ];
    document.getElementById("taskCards").innerHTML = definitions.map(([key, title, description]) => {
        const task = tasks[key] || { status: "idle" };
        let progress = task.status === "completed" ? 100 : 0;
        if (key === "scan_market" && ["running", "paused"].includes(latestScan?.status) && latestScan.total_stocks) {
            progress = Math.round(latestScan.processed_stocks / latestScan.total_stocks * 100);
        }
        const indeterminate = task.status === "running" && key !== "scan_market";
        const progressCopy = taskProgressCopy(key, task, latestScan, progress);
        return `<article class="card task-card"><div class="task-card-head"><h3>${title}</h3>
            <span class="status-pill ${task.status}">${statusLabel(task.status)}</span></div><p>${description}</p>
            <div class="progress-track ${task.status} ${indeterminate ? "indeterminate" : ""}">
            <span style="width:${progress}%"></span></div>
            <p>${progressCopy}</p></article>`;
    }).join("");
    const scanRunning = ["running", "paused"].includes(tasks.scan_market?.status);
    const refreshRunning = tasks.refresh_stocks?.status === "running";
    const refreshEtfsRunning = tasks.refresh_etfs?.status === "running";
    const retryRunning = ["running", "paused"].includes(tasks.retry_errors?.status);
    document.querySelectorAll('[data-action="scan"], #startScanButton').forEach((button) => {
        button.disabled = scanRunning;
    });
    document.querySelectorAll('[data-action="refresh-stocks"], #refreshStocksButton').forEach((button) => {
        button.disabled = refreshRunning;
    });
    document.querySelectorAll('[data-action="refresh-etfs"]').forEach((button) => {
        button.disabled = refreshEtfsRunning;
    });
    const retryButton = document.getElementById("retryErrorsButton");
    if (retryButton && state.errorDetail) retryButton.disabled = retryRunning || !state.errorDetail.can_retry;
}

function taskProgressCopy(key, task, latestScan, progress) {
    if (key === "scan_market" && ["running", "paused"].includes(task.status) && ["running", "paused"].includes(latestScan?.status)) {
        return `已处理 ${latestScan.processed_stocks}/${latestScan.total_stocks} 个标的 · ${progress}%`;
    }
    if (task.status === "paused") return "任务已暂停，可在任务队列中继续";
    if (key === "retry_errors" && task.status === "running") {
        return `正在重试扫描任务 #${task.source_run_id || task.run_id} 的失败标的`;
    }
    if (key === "retry_errors" && task.status === "completed" && task.result) {
        return `已解决 ${task.result.resolved_count} 条，仍失败 ${task.result.failed_count} 条`;
    }
    if (task.status === "running") return "任务正在执行，进度将自动更新";
    if (task.finished_at) return `${statusLabel(task.status)}于 ${formatDate(task.finished_at)}`;
    return task.started_at ? `开始于 ${formatDate(task.started_at)}` : "等待手动启动";
}

async function openErrorModal(runId) {
    if (!document.getElementById("errorModal")) {
        toast("服务将在当前扫描完成后加载错误详情功能", true);
        return;
    }
    state.selectedErrorRun = runId;
    state.errorDetail = null;
    const modal = document.getElementById("errorModal");
    modal.hidden = false;
    syncModalOpenState();
    document.getElementById("errorModalSubtitle").textContent = `扫描任务 #${runId} · 正在读取错误记录`;
    document.getElementById("errorSummary").innerHTML = "";
    document.getElementById("errorReasonGroups").innerHTML = "";
    document.getElementById("errorRows").innerHTML = '<tr><td colspan="5" class="table-empty">正在加载错误详情…</td></tr>';
    document.getElementById("runErrorMessage").hidden = true;
    document.getElementById("retryErrorsButton").disabled = true;
    await loadErrorDetail(runId);
    scheduleTaskPoll(2500);
    modal.querySelector(".modal-close").focus();
}

function closeErrorModal() {
    document.getElementById("errorModal").hidden = true;
    state.selectedErrorRun = null;
    state.errorDetail = null;
    syncModalOpenState();
    if (!hasRunningTasks() && state.currentView !== "runs") cancelTaskPoll();
}

async function loadErrorDetail(runId, silent = false) {
    try {
        const data = await api(`/api/scan-runs/${runId}/errors?limit=500`);
        if (state.selectedErrorRun !== runId) return;
        state.errorDetail = data;
        renderErrorDetail(data);
    } catch (error) {
        if (!silent) toast(error.message, true);
        document.getElementById("errorRows").innerHTML = '<tr><td colspan="5" class="table-empty">错误详情读取失败</td></tr>';
    }
}

function renderErrorDetail(data) {
    const summary = data.summary;
    const retryTotal = data.items.reduce((total, item) => total + item.retry_count, 0);
    document.getElementById("errorModalSubtitle").textContent = `扫描任务 #${data.run.id} · ${statusLabel(data.run.status)}`;
    document.getElementById("errorSummary").innerHTML = [
        ["未解决错误", summary.unresolved + summary.untracked, "var(--red)"],
        ["已重试解决", summary.resolved, "var(--green)"],
        ["错误类型", summary.groups.length, "var(--amber)"],
        ["累计重试", retryTotal, "var(--cyan)"],
    ].map(([label, value, color]) => `
        <div class="error-summary-card"><span>${label}</span><strong style="color:${color}">${value}</strong></div>
    `).join("");
    document.getElementById("errorReasonGroups").innerHTML = summary.groups.length
        ? summary.groups.slice(0, 8).map((item) => `
            <div class="error-reason-item"><strong>${escapeHtml(item.error_type)} · ${item.count} 条</strong>
            <span>${escapeHtml(item.error_message)}</span></div>
        `).join("")
        : '<span class="quiet-label">暂无可归类的逐标的错误</span>';
    const runMessage = document.getElementById("runErrorMessage");
    runMessage.hidden = !data.run.error_message;
    runMessage.textContent = data.run.error_message ? `任务级错误：${data.run.error_message}` : "";
    const rows = document.getElementById("errorRows");
    rows.innerHTML = data.items.length ? data.items.map((item) => `
        <tr><td><div class="stock-cell"><strong>${escapeHtml(item.stock_name)}</strong><span>${escapeHtml(item.ts_code)}</span></div></td>
        <td>${escapeHtml(item.error_category || item.last_error_type)}</td><td class="error-message-cell">
        <strong>${escapeHtml(item.error_summary || item.last_error_message)}</strong>
        <details class="raw-error-detail"><summary>查看原始错误</summary><p>${escapeHtml(item.last_error_message)}</p></details></td>
        <td>${item.retry_count} 次</td><td><span class="status-pill ${item.status}">${errorStatusLabel(item.status)}</span></td></tr>
    `).join("") : '<tr><td colspan="5" class="table-empty">该批次没有逐标的错误记录</td></tr>';
    const retryButton = document.getElementById("retryErrorsButton");
    const retryRunning = ["running", "paused"].includes(state.tasks.retry_errors?.status);
    retryButton.disabled = !data.can_retry || retryRunning;
    retryButton.textContent = retryRunning ? "正在重试…" : `重试失败标的${summary.unresolved ? `（${summary.unresolved}）` : ""}`;
    document.getElementById("retryHint").textContent = data.can_retry
        ? "仅重试尚未解决的股票，不会重复扫描全市场。"
        : errorRetryHint(data);
}

function errorRetryHint(data) {
    if (data.summary.untracked) return `其中 ${data.summary.untracked} 条为旧版扫描错误，没有保存股票明细，无法单独重试。`;
    if (data.run.error_message) return "这是任务级中断，没有可单独重试的股票。";
    return "当前没有未解决的标的错误。";
}

async function retrySelectedErrors() {
    if (!state.selectedErrorRun) return;
    const button = document.getElementById("retryErrorsButton");
    button.disabled = true;
    button.textContent = "正在启动重试…";
    try {
        const data = await api(`/api/scan-runs/${state.selectedErrorRun}/retry-errors`, { method: "POST", body: "{}" });
        state.tasks.retry_errors = data.task;
        toast("失败标的重试已加入任务队列");
        renderErrorDetail(state.errorDetail);
        scheduleTaskPoll(0, true);
    } catch (error) {
        toast(error.message, true);
        button.disabled = false;
        button.textContent = "重试失败标的";
    }
}

async function startTask(taskName, scanScope = "all") {
    if (taskName === "scan") {
        const scopeLabel = { stock: "股票", etf: "ETF", all: "全市场" }[scanScope] || "全市场";
        const scanTarget = scanScope === "all" ? "全部股票和ETF" : `全部${scopeLabel}`;
        const confirmed = await confirmAction({
            title: `启动${scopeLabel}扫描`,
            message: `扫描会遍历${scanTarget}并按需补充历史行情，执行时间取决于本地缓存和数据源状态。`,
            confirmText: "开始扫描",
            tone: "primary",
        });
        if (!confirmed) return;
    }
    const paths = {
        scan: "/api/tasks/scan-market",
        "refresh-stocks": "/api/tasks/refresh-stocks",
        "refresh-etfs": "/api/tasks/refresh-etfs",
    };
    try {
        const body = taskName === "scan" ? JSON.stringify({ scan_scope: scanScope }) : "{}";
        const data = await api(paths[taskName], { method: "POST", body });
        const taskKey = { scan: "scan_market", "refresh-stocks": "refresh_stocks", "refresh-etfs": "refresh_etfs" }[taskName];
        if (taskKey && data.task) state.tasks[taskKey] = data.task;
        toast(data.started ? "任务已在后台启动" : "任务已经在运行");
        if (taskName === "scan" && data.started) showView("runs");
        scheduleTaskPoll(0, true);
    } catch (error) {
        toast(error.message, true);
    }
}

function cancelTaskPoll() {
    window.clearTimeout(state.taskPollTimer);
    state.taskPollTimer = null;
}

function scheduleTaskPoll(delay, force = false) {
    cancelTaskPoll();
    if (!force && !shouldPollTasks()) return;
    state.taskPollTimer = window.setTimeout(pollTasks, delay);
}

function hasRunningTasks(tasks = state.tasks) {
    return Object.values(tasks || {}).some((task) => ["running", "paused"].includes(task?.status));
}

function shouldPollTasks(running = hasRunningTasks()) {
    // 只有任务页、错误弹窗或确有后台任务运行时，才继续请求任务进度接口。
    return state.currentView === "runs" || Boolean(state.selectedErrorRun) || running;
}

async function pollTasks() {
    let nextDelay = 10000;
    let shouldContinue = shouldPollTasks();
    try {
        // 任务运行时提高轮询频率，并同步刷新任务卡片与扫描历史中的进度。
        const data = await api("/api/task-progress?limit=20");
        renderTasks(data.tasks, data.latest_scan);
        if (state.currentView === "runs") renderRunRows(data.task_runs || data.scan_runs);
        if (state.selectedErrorRun) await loadErrorDetail(state.selectedErrorRun, true);
        const running = hasRunningTasks(data.tasks);
        nextDelay = running ? 2500 : 10000;
        shouldContinue = shouldPollTasks(running);
        if (!running && state.dashboard) {
            const finishedAfterDashboard = Object.values(data.tasks).some((task) => {
                return task.finished_at && task.finished_at > state.dashboard.generated_at;
            });
            if (finishedAfterDashboard) await loadDashboard();
        }
    } catch (_error) {
        // 页面可能正在关闭，轮询失败不需要打扰用户。
    } finally {
        if (shouldContinue) scheduleTaskPoll(nextDelay);
        else cancelTaskPoll();
    }
}

function toast(message, isError = false) {
    const element = document.createElement("div");
    element.className = `toast${isError ? " error" : ""}`;
    element.textContent = message;
    document.getElementById("toastRegion").appendChild(element);
    setTimeout(() => element.remove(), 4200);
}

function sourceDisplayName(source) {
    const names = {
        baostock: "BaoStock", eastmoney: "东方财富", yahoo: "Yahoo Finance", sge: "上海黄金交易所",
        sina: "新浪行情", tencent: "腾讯财经", sqlite: "SQLite", tushare: "Tushare",
    };
    return names[String(source || "").toLowerCase()] || source || "未知";
}

function statusLabel(status) {
    return { running: "运行中", paused: "已暂停", completed: "已完成", failed: "失败", idle: "待命" }[status] || status || "待命";
}

function errorStatusLabel(status) {
    return { failed: "待重试", resolved: "已解决" }[status] || status || "未知";
}

function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
}

function formatMarketPrice(value) {
    const number = numberOrNull(value);
    if (number === null) return "—";
    return number.toFixed(Math.abs(number) < 10 ? 3 : 2);
}

function formatCompactMarketValue(value, suffix = "") {
    const number = numberOrNull(value);
    if (number === null) return "—";
    if (Math.abs(number) >= 100000000) return `${(number / 100000000).toFixed(2)}亿${suffix}`;
    if (Math.abs(number) >= 10000) return `${(number / 10000).toFixed(2)}万${suffix}`;
    return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(number)}${suffix}`;
}

function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes < 1024) return `${bytes} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let amount = bytes;
    let unitIndex = -1;
    do {
        amount /= 1024;
        unitIndex += 1;
    } while (amount >= 1024 && unitIndex < units.length - 1);
    return `${amount.toFixed(amount >= 100 ? 0 : amount >= 10 ? 1 : 2)} ${units[unitIndex]}`;
}

function formatPercent(value) {
    const number = Number(value);
    return `${number >= 0 ? "+" : ""}${number.toFixed(2)}%`;
}

function formatDate(value) {
    if (!value) return "—";
    const normalized = String(value).includes("T") ? value : String(value).replace(" ", "T");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour12: false });
}

function formatListDate(value) {
    const text = String(value || "");
    return /^\d{8}$/.test(text) ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}` : text || "—";
}

function numberOrNull(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    })[character]);
}

function debounce(callback, delay) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => callback(...args), delay);
    };
}
