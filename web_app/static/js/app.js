const state = {
    dashboard: null,
    indicators: [],
    selectedSignal: "",
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
    taskPollTimer: null,
    tasks: {},
    selectedErrorRun: null,
    errorDetail: null,
    databaseSettings: null,
    confirmationResolver: null,
};

const viewMeta = {
    overview: ["MARKET INTELLIGENCE", "监控总览"],
    signals: ["DYNAMIC INDICATORS", "策略信号"],
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
    window.addEventListener("resize", debounce(() => drawChart(state.chartPoints, state.chartItem), 180));
    scheduleTaskPoll(1000);
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
}

function showView(viewName) {
    document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === viewName));
    document.querySelectorAll("[data-view-target]").forEach((button) => {
        button.classList.toggle("active", button.dataset.viewTarget === viewName);
    });
    const [eyebrow, title] = viewMeta[viewName] || viewMeta.overview;
    document.getElementById("pageEyebrow").textContent = eyebrow;
    document.getElementById("pageTitle").textContent = title;
    location.hash = viewName;
    if (viewName === "signals") loadSignals();
    if (viewName === "stocks") loadStocks();
    if (viewName === "etfs") loadEtfs();
    if (viewName === "runs") loadRuns();
    if (viewName === "settings") loadDatabaseSettings();
    window.scrollTo({ top: 0, behavior: "smooth" });
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
        if (confirmModal && !confirmModal.hidden) resolveConfirmation(false);
        else if (assignModal && !assignModal.hidden) closeAssignGroupModal();
        else if (groupModal && !groupModal.hidden) closeGroupModal();
        else if (errorModal && !errorModal.hidden) closeErrorModal();
    });
}

function bindFilters() {
    document.getElementById("signalSearch").addEventListener("input", debounce(loadSignals, 280));
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
        document.getElementById("syncCopy").textContent = `更新于 ${formatTime(data.generated_at)}`;
    } catch (error) {
        toast(error.message, true);
        document.getElementById("syncCopy").textContent = "读取失败";
    } finally {
        reloadButton.classList.remove("spinning");
    }
}

function renderMarket(market) {
    const badge = document.querySelector(".market-badge");
    badge.classList.toggle("closed", !market.is_open);
    document.getElementById("marketLabel").textContent = `${market.label} · ${market.time}`;
}

function renderMetrics(data) {
    const stats = data.stats || {};
    const scan = data.latest_scan || {};
    const totalSignals = (data.indicators || []).reduce((total, item) => total + item.count, 0);
    document.getElementById("heroSignalCount").textContent = formatNumber(scan.matched_stocks || 0);
    const metrics = [
        ["股票主数据", stats.stock_count || 0, `另有 ${formatNumber(stats.etf_count || 0)} 只ETF`, "◎", "var(--green)"],
        ["本轮命中股票", scan.matched_stocks || 0, scan.status ? statusLabel(scan.status) : "尚未执行扫描", "◇", "var(--cyan)"],
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
            showView("signals");
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
        rows.innerHTML = '<tr><td colspan="3" class="table-empty">完成首次扫描后，这里会显示最新命中股票</td></tr>';
        return;
    }
    rows.innerHTML = items.map((item) => `
        <tr><td><div class="stock-cell"><strong>${escapeHtml(item.stock_name)}</strong><span>${escapeHtml(item.ts_code)}</span></div></td>
        <td><span class="signal-tag">${escapeHtml(item.signal_type)}</span></td><td>#${item.run_id}</td></tr>
    `).join("");
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
    const lineColor = styles.getPropertyValue("--green").trim();
    const border = styles.getPropertyValue("--border").trim();
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
        context.fillText(item ? "暂无本地K线，运行监控后将显示走势" : "选择ETF查看走势", width / 2, height / 2);
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
    const configured = ["baostock", "eastmoney", "sina", "sqlite"];
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
    const items = [{ label: "全部指标", count: null }, ...state.indicators];
    container.innerHTML = items.map((item) => `
        <button class="filter-chip ${state.selectedSignal === (item.key ? item.label : "") ? "active" : ""}"
        data-signal="${item.key ? escapeHtml(item.label) : ""}" type="button">${escapeHtml(item.label)}${item.count == null ? "" : ` · ${item.count}`}</button>
    `).join("");
    container.querySelectorAll(".filter-chip").forEach((button) => {
        button.addEventListener("click", () => {
            state.selectedSignal = button.dataset.signal;
            renderIndicatorFilters();
            loadSignals();
        });
    });
}

async function loadSignals() {
    const query = document.getElementById("signalSearch").value.trim();
    const params = new URLSearchParams({ limit: "500", q: query });
    if (state.selectedSignal) params.set("type", state.selectedSignal);
    try {
        const data = await api(`/api/signals?${params}`);
        const rows = document.getElementById("signalRows");
        document.getElementById("signalResultCount").textContent = `${data.total} 条`;
        document.getElementById("signalTableTitle").textContent = state.selectedSignal || "全部指标";
        if (!data.items.length) {
            rows.innerHTML = '<tr><td colspan="4" class="table-empty">当前条件下暂无命中股票</td></tr>';
            return;
        }
        rows.innerHTML = data.items.map((item) => `
            <tr><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${escapeHtml(item.stock_name)}</td>
            <td><span class="signal-tag">${escapeHtml(item.signal_type)}</span></td><td>${formatDate(item.created_at)}</td></tr>
        `).join("");
    } catch (error) {
        toast(error.message, true);
    }
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
                <tr><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${escapeHtml(item.name)}</td>
                <td>${escapeHtml(item.market || "—")}</td><td>${formatListDate(item.list_date)}</td>
                <td>${sourceDisplayName(item.source)}</td><td>${instrumentActions("stock", item)}</td></tr>
            `).join("") : '<tr><td colspan="6" class="table-empty">当前列表没有匹配的股票</td></tr>';
            bindInstrumentActions(rows);
        } else {
            rows.innerHTML = data.items.length ? data.items.map((item) => `
                <tr><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${escapeHtml(item.name)}</td>
                <td>${escapeHtml(item.market || "—")}</td><td>${formatListDate(item.list_date)}</td>
                <td>${sourceDisplayName(item.source)}</td></tr>
            `).join("") : '<tr><td colspan="5" class="table-empty">当前列表没有匹配的股票</td></tr>';
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
            <tr><td><strong>${escapeHtml(item.ts_code)}</strong></td><td>${escapeHtml(item.name)}</td>
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
    if (!groupId) {
        return `<button class="row-action" data-assign-asset="${assetType}" data-code="${code}" data-name="${name}" type="button">加入分组</button>`;
    }
    const pinLabel = item.is_pinned ? "取消置顶" : "置顶";
    const pinClass = item.is_pinned ? "row-action pinned" : "row-action";
    return `<div class="row-actions"><button class="${pinClass}" data-pin-asset="${assetType}" data-code="${code}"
        data-pinned="${item.is_pinned ? "true" : "false"}" type="button">${item.is_pinned ? "★" : "☆"} ${pinLabel}</button>
        <button class="row-action danger-action" data-remove-asset="${assetType}" data-code="${code}" type="button">移出分组</button></div>`;
}

function bindInstrumentActions(container) {
    container.querySelectorAll("[data-assign-asset]").forEach((button) => {
        button.addEventListener("click", () => openAssignGroupModal(button.dataset.assignAsset, button.dataset.code, button.dataset.name));
    });
    container.querySelectorAll("[data-pin-asset]").forEach((button) => {
        button.addEventListener("click", () => toggleInstrumentPin(button.dataset.pinAsset, button.dataset.code, button.dataset.pinned));
    });
    container.querySelectorAll("[data-remove-asset]").forEach((button) => {
        button.addEventListener("click", () => removeInstrumentFromGroup(button.dataset.removeAsset, button.dataset.code));
    });
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
        const hasError = item.error_count > 0 || Boolean(item.error_message);
        const errorLabel = item.error_count > 0 ? `${item.error_count} 条` : "查看原因";
        const isRetry = item.task_type === "error_retry";
        const taskLabel = isRetry ? "错误股票重试" : "全市场扫描";
        const taskSource = isRetry
            ? item.parent_run_id ? `来源扫描任务 #${item.parent_run_id}` : "原扫描任务已删除"
            : "全市场股票";
        const detailRunId = isRetry ? item.parent_run_id : item.id;
        const resultLabel = isRetry ? `${item.matched_stocks} 条已解决` : `${item.matched_stocks} 只命中`;
        let errorCell = `<span class="quiet-label">0</span>`;
        if (item.error_count > 0 && supportsErrorModal && detailRunId) {
            errorCell = `<button class="error-count-button has-error" data-error-run="${detailRunId}" type="button">${errorLabel}</button>`;
        } else if (hasError) {
            errorCell = `<span class="status-pill failed" title="${escapeHtml(item.error_message || "存在未解决错误")}">${errorLabel}</span>`;
        }
        return `<tr class="scan-row ${isRunning ? "is-running" : ""}"><td>
            <div class="stock-cell"><strong>#${item.id} · ${taskLabel}</strong><span>${taskSource}</span></div></td>
            <td><span class="status-pill ${item.status}">${statusLabel(item.status)}</span></td>
            <td><div class="table-progress"><div class="progress-copy"><strong>${item.processed_stocks}/${item.total_stocks}</strong>
            <span>${progress}%</span></div><div class="mini-progress ${isRunning ? "running" : ""}">
            <span style="width:${progress}%"></span></div></div></td><td><strong>${resultLabel}</strong></td>
            <td>${errorCell}</td><td>${formatDate(item.started_at)}</td><td>
            ${isRunning ? '<span class="quiet-label">执行中</span>' : `<button class="row-action danger-action"
            data-delete-run="${item.id}" type="button">删除</button>`}</td></tr>`;
    }).join("") : '<tr><td colspan="7" class="table-empty">暂无任务记录</td></tr>';
    rows.querySelectorAll("[data-error-run]").forEach((button) => {
        button.addEventListener("click", () => openErrorModal(Number(button.dataset.errorRun)));
    });
    rows.querySelectorAll("[data-delete-run]").forEach((button) => {
        button.addEventListener("click", () => deleteTaskRun(Number(button.dataset.deleteRun), button));
    });
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
        ["retry_errors", "错误股票重试", "只重新处理扫描失败的股票列表"],
    ];
    document.getElementById("taskCards").innerHTML = definitions.map(([key, title, description]) => {
        const task = tasks[key] || { status: "idle" };
        let progress = task.status === "completed" ? 100 : 0;
        if (key === "scan_market" && latestScan?.status === "running" && latestScan.total_stocks) {
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
    const scanRunning = tasks.scan_market?.status === "running";
    const refreshRunning = tasks.refresh_stocks?.status === "running";
    const refreshEtfsRunning = tasks.refresh_etfs?.status === "running";
    const retryRunning = tasks.retry_errors?.status === "running";
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
    if (retryButton && state.errorDetail) retryButton.disabled = retryRunning || !state.errorDetail.can_retry || scanRunning;
}

function taskProgressCopy(key, task, latestScan, progress) {
    if (key === "scan_market" && task.status === "running" && latestScan?.status === "running") {
        return `已处理 ${latestScan.processed_stocks}/${latestScan.total_stocks} 只 · ${progress}%`;
    }
    if (key === "retry_errors" && task.status === "running") {
        return `正在重试扫描任务 #${task.run_id} 的失败股票`;
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
    modal.querySelector(".modal-close").focus();
}

function closeErrorModal() {
    document.getElementById("errorModal").hidden = true;
    state.selectedErrorRun = null;
    state.errorDetail = null;
    syncModalOpenState();
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
        : '<span class="quiet-label">暂无可归类的逐股票错误</span>';
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
    `).join("") : '<tr><td colspan="5" class="table-empty">该批次没有逐股票错误记录</td></tr>';
    const retryButton = document.getElementById("retryErrorsButton");
    const retryRunning = state.tasks.retry_errors?.status === "running";
    const scanRunning = state.tasks.scan_market?.status === "running";
    retryButton.disabled = !data.can_retry || retryRunning || scanRunning;
    retryButton.textContent = retryRunning ? "正在重试…" : `重试失败股票${summary.unresolved ? `（${summary.unresolved}）` : ""}`;
    document.getElementById("retryHint").textContent = data.can_retry
        ? "仅重试尚未解决的股票，不会重复扫描全市场。"
        : errorRetryHint(data);
}

function errorRetryHint(data) {
    if (data.summary.untracked) return `其中 ${data.summary.untracked} 条为旧版扫描错误，没有保存股票明细，无法单独重试。`;
    if (data.run.error_message) return "这是任务级中断，没有可单独重试的股票。";
    return "当前没有未解决的股票错误。";
}

async function retrySelectedErrors() {
    if (!state.selectedErrorRun) return;
    const button = document.getElementById("retryErrorsButton");
    button.disabled = true;
    button.textContent = "正在启动重试…";
    try {
        const data = await api(`/api/scan-runs/${state.selectedErrorRun}/retry-errors`, { method: "POST", body: "{}" });
        state.tasks.retry_errors = data.task;
        toast("失败股票重试已加入任务队列");
        renderErrorDetail(state.errorDetail);
        scheduleTaskPoll(0);
    } catch (error) {
        toast(error.message, true);
        button.disabled = false;
        button.textContent = "重试失败股票";
    }
}

async function startTask(taskName) {
    if (taskName === "scan") {
        const confirmed = await confirmAction({
            title: "启动全市场扫描",
            message: "扫描会遍历全部股票并按需补充历史行情，执行时间取决于本地缓存和数据源状态。",
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
        const data = await api(paths[taskName], { method: "POST", body: "{}" });
        toast(data.started ? "任务已在后台启动" : "任务已经在运行");
        scheduleTaskPoll(0);
    } catch (error) {
        toast(error.message, true);
    }
}

function scheduleTaskPoll(delay) {
    window.clearTimeout(state.taskPollTimer);
    state.taskPollTimer = window.setTimeout(pollTasks, delay);
}

async function pollTasks() {
    let nextDelay = 10000;
    try {
        // 任务运行时提高轮询频率，并同步刷新任务卡片与扫描历史中的进度。
        const data = await api("/api/task-progress?limit=20");
        renderTasks(data.tasks, data.latest_scan);
        renderRunRows(data.task_runs || data.scan_runs);
        if (state.selectedErrorRun) await loadErrorDetail(state.selectedErrorRun, true);
        const running = Object.values(data.tasks).some((task) => task.status === "running");
        nextDelay = running ? 2500 : 10000;
        if (!running && state.dashboard) {
            const finishedAfterDashboard = Object.values(data.tasks).some((task) => {
                return task.finished_at && task.finished_at > state.dashboard.generated_at;
            });
            if (finishedAfterDashboard) await loadDashboard();
        }
    } catch (_error) {
        // 页面可能正在关闭，轮询失败不需要打扰用户。
    } finally {
        scheduleTaskPoll(nextDelay);
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
    const names = { baostock: "BaoStock", eastmoney: "东方财富", sina: "新浪行情", sqlite: "SQLite", tushare: "Tushare" };
    return names[String(source || "").toLowerCase()] || source || "未知";
}

function statusLabel(status) {
    return { running: "运行中", completed: "已完成", failed: "失败", idle: "待命" }[status] || status || "待命";
}

function errorStatusLabel(status) {
    return { failed: "待重试", resolved: "已解决" }[status] || status || "未知";
}

function formatNumber(value) {
    return new Intl.NumberFormat("zh-CN").format(Number(value) || 0);
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
