(() => {
    const colors = {
        up: "#16845f", down: "#cf5b5b", center: "#315bce",
        high14: "#d18a21", high30: "#8b55bd", low: "#138a9b",
        discovery: "#d28220", grid: "#e8edf4", text: "#728096",
    };

    function sizeCanvas(canvas, height) {
        const width = canvas.clientWidth;
        const ratio = Math.min(window.devicePixelRatio || 1, 3);
        canvas.style.height = `${height}px`;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        const ctx = canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        return {ctx, width};
    }

    function drawSparklines() {
        document.querySelectorAll(".dm-trend-spark").forEach(canvas => {
            const values = (canvas.dataset.closes || "").split(",").map(Number).filter(Number.isFinite);
            if (values.length < 2 || !canvas.clientWidth) return;
            const {ctx, width} = sizeCanvas(canvas, 72);
            const min = Math.min(...values), max = Math.max(...values);
            const spread = Math.max(max - min, Math.abs(max) * .01, Number.EPSILON);
            const y = value => 58 - (value - min) / spread * 44;
            const x = index => 5 + index * (width - 10) / (values.length - 1);
            const rising = values.at(-1) >= values[0];
            ctx.beginPath();
            values.forEach((value, index) => index ? ctx.lineTo(x(index), y(value)) : ctx.moveTo(x(index), y(value)));
            ctx.lineWidth = 2;
            ctx.strokeStyle = rising ? colors.up : colors.down;
            ctx.stroke();
            ctx.beginPath(); ctx.arc(x(values.length - 1), y(values.at(-1)), 3.5, 0, Math.PI * 2);
            ctx.fillStyle = ctx.strokeStyle; ctx.fill();
        });
    }

    const canvas = document.getElementById("dm-trend-candles");
    const candleSource = document.getElementById("dm-trend-candle-data");
    const eventSource = document.getElementById("dm-trend-event-data");
    const indicatorSource = document.getElementById("dm-trend-indicator-data");
    const meta = document.getElementById("dm-trend-meta");
    const tooltip = document.getElementById("dm-trend-tooltip");
    const rows = candleSource ? JSON.parse(candleSource.textContent).map(row => ({
        ...row, open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close),
        quoteVolume: Number(row.quote_volume || 0),
    })).filter(row => [row.open, row.high, row.low, row.close].every(Number.isFinite)) : [];
    const events = new Map(eventSource ? JSON.parse(eventSource.textContent).map(event => [event.date, event]) : []);
    const indicators = new Map(indicatorSource ? JSON.parse(indicatorSource.textContent).map(item => [item.date, item]) : []);
    let range = window.innerWidth < 600 ? "30" : "90";
    let includeWicks = false;
    let logScale = true;
    let selectedDate = null;
    let geometry = null;
    let frame = null;

    function fmt(value) {
        if (!Number.isFinite(value)) return "—";
        return value.toPrecision(6).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
    }

    function requestDraw() {
        if (frame !== null) return;
        frame = requestAnimationFrame(() => { frame = null; draw(); });
    }

    function activeRows() {
        return range === "all" ? rows : rows.slice(-Number(range));
    }

    function updateTooltip(row, event) {
        if (!row) { tooltip.hidden = true; return; }
        const signal = events.get(row.date);
        const indicator = indicators.get(row.date);
        const startClose = geometry.visible[0].close;
        const periodChange = startClose > 0 ? (row.close / startClose - 1) * 100 : 0;
        const dailyChange = row.open > 0 ? (row.close / row.open - 1) * 100 : 0;
        const parts = [
            `<strong>${row.date} UTC</strong>`,
            `<dl><div><dt>开 / 高</dt><dd>${fmt(row.open)} / ${fmt(row.high)}</dd></div>`,
            `<div><dt>低 / 收</dt><dd>${fmt(row.low)} / ${fmt(row.close)}</dd></div>`,
            `<div><dt>当日涨跌</dt><dd>${dailyChange >= 0 ? "+" : ""}${dailyChange.toFixed(2)}%</dd></div>`,
            `<div><dt>区间累计</dt><dd>${periodChange >= 0 ? "+" : ""}${periodChange.toFixed(1)}%</dd></div>`,
            `<div><dt>价格中心</dt><dd>${indicator?.center ? fmt(Number(indicator.center)) : "—"}</dd></div>`,
            `<div><dt>计价成交额</dt><dd>${row.quoteVolume ? `${(row.quoteVolume / 10000).toFixed(1)} 万` : "—"}</dd></div>`,
            `<div><dt>近14日新高</dt><dd>${indicator?.recent14_high_count ?? "—"} 次</dd></div>`,
            `<div><dt>阶段</dt><dd>${signal?.stage || "发现前"}</dd></div>`,
            `<div><dt>事件</dt><dd>${signal?.new30 ? "正式观察：30 日收盘新高" : signal?.new14 ? "正式观察：14 日收盘新高" : indicator?.high30 ? "历史回看：30 日收盘新高" : indicator?.high14 ? "历史回看：14 日收盘新高" : indicator?.pivot_low ? "历史回看：已确认低点" : "—"}</dd></div></dl>`,
        ];
        // All values above come from server-serialized numeric/date fields; keep user text out of markup.
        tooltip.innerHTML = parts.join("");
        tooltip.hidden = false;
        const px = geometry.x(geometry.visible.findIndex(item => item.date === row.date));
        const cardWidth = tooltip.offsetWidth;
        const cardHeight = tooltip.offsetHeight;
        tooltip.style.left = `${Math.max(0, Math.min(geometry.width - cardWidth, px + 16 + cardWidth < geometry.width ? px + 16 : px - cardWidth - 16))}px`;
        tooltip.style.top = `${Math.max(0, Math.min(geometry.height - cardHeight, (event?.clientY ?? 0) - (canvas.getBoundingClientRect().top) + 10))}px`;
    }

    function draw() {
        if (!canvas || !rows.length || !canvas.clientWidth) return;
        const visible = activeRows();
        const compact = canvas.clientWidth < 560;
        const height = compact ? 560 : 690;
        const {ctx, width} = sizeCanvas(canvas, height);
        const left = compact ? 66 : 84, right = width - (compact ? 53 : 80);
        const top = 28, priceBottom = compact ? 330 : 410;
        const volumeTop = priceBottom + 28, volumeBottom = volumeTop + (compact ? 64 : 85);
        const countTop = volumeBottom + 28, countBottom = height - 35;
        const plotValues = visible.flatMap(row => includeWicks ? [row.low, row.high] : [row.open, row.close]);
        const centers = visible.map(row => Number(indicators.get(row.date)?.center)).filter(value => Number.isFinite(value) && value > 0);
        const floor = Math.min(...plotValues, ...centers);
        const ceiling = Math.max(...plotValues, ...centers);
        const pad = Math.max((ceiling - floor) * .06, ceiling * .012, Number.EPSILON);
        const min = logScale ? floor * .9 : Math.max(0, floor - pad);
        const max = logScale ? ceiling * 1.1 : ceiling + pad;
        const transform = value => logScale ? Math.log(value) : value;
        const untransform = value => logScale ? Math.exp(value) : value;
        const tMin = transform(min), tMax = transform(max);
        const y = value => priceBottom - (transform(Math.max(value, min)) - tMin) / (tMax - tMin) * (priceBottom - top);
        const x = index => left + (index + .5) * (right - left) / visible.length;
        geometry = {visible, width, height, left, right, top, bottom: countBottom, x};
        const periodChange = (visible.at(-1).close / visible[0].close - 1) * 100;
        const peakClose = Math.max(...visible.map(row => row.close));
        const fromPeak = (visible.at(-1).close / peakClose - 1) * 100;
        const rangeStats = document.getElementById("dm-trend-range-stats");
        if (rangeStats) rangeStats.innerHTML = `<span>${visible[0].date} → ${visible.at(-1).date}</span><span>区间收盘变化 <strong class="${periodChange >= 0 ? "dm-positive" : "dm-negative"}">${periodChange >= 0 ? "+" : ""}${periodChange.toFixed(1)}%</strong></span><span>距区间最高收盘 <strong class="dm-negative">${fromPeak.toFixed(1)}%</strong></span>`;
        ctx.font = "11px system-ui, sans-serif";
        for (let tick = 0; tick <= 4; tick++) {
            const price = untransform(tMin + (tMax - tMin) * tick / 4), py = y(price);
            ctx.strokeStyle = colors.grid; ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke();
            ctx.fillStyle = colors.text; ctx.textAlign = "right";
            ctx.fillText(fmt(price), left - 7, py + 4);
            ctx.textAlign = "left";
            const pct = (price / visible[0].close - 1) * 100;
            ctx.fillText(`${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`, right + 6, py + 4);
        }
        ctx.fillStyle = colors.text; ctx.textAlign = "left";
        ctx.fillText(logScale ? "价格 · 对数" : "价格 · 线性", left, top - 13);
        ctx.textAlign = "right"; ctx.fillText("较区间首日", right + 50, top - 13);
        ctx.strokeStyle = "#d7e0eb";
        [volumeTop - 13, countTop - 13].forEach(py => {
            ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke();
        });
        const maxVolume = Math.max(1, ...visible.map(row => row.quoteVolume));
        const volumeY = value => volumeBottom - value / maxVolume * (volumeBottom - volumeTop);
        const maxCount = Math.max(1, ...visible.map(row => indicators.get(row.date)?.recent14_high_count || 0));
        const countY = value => countBottom - value / maxCount * (countBottom - countTop);
        ctx.fillStyle = colors.text; ctx.textAlign = "left";
        ctx.fillText(`成交额 · 万${meta.dataset.quote || "计价币"}`, left, volumeTop - 12);
        ctx.fillText("近14日收盘新高次数", left, countTop - 12);
        ctx.textAlign = "right";
        ctx.fillText((maxVolume / 10000).toFixed(maxVolume >= 10000000 ? 0 : 1), left - 6, volumeTop + 4);
        ctx.fillText("0", left - 6, volumeBottom + 3);
        ctx.fillText(String(maxCount), left - 6, countTop + 4);
        ctx.fillText("0", left - 6, countBottom + 3);
        const discoveryIndex = visible.findIndex(row => row.date === meta.dataset.discovery);
        if (discoveryIndex >= 0) {
            const px = x(discoveryIndex);
            ctx.strokeStyle = colors.discovery; ctx.setLineDash([4, 4]);
            ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, countBottom); ctx.stroke(); ctx.setLineDash([]);
            ctx.fillStyle = colors.discovery; ctx.textAlign = "center";
            ctx.fillText("首次发现", Math.max(left + 30, Math.min(right - 30, px)), top - 9);
        }
        const step = (right - left) / visible.length;
        visible.forEach((row, index) => {
            const px = x(index), signal = events.get(row.date);
            const color = row.close >= row.open ? colors.up : colors.down;
            ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = Math.max(1, Math.min(2, step * .2));
            ctx.beginPath(); ctx.moveTo(px, Math.max(top, y(row.high))); ctx.lineTo(px, Math.min(priceBottom, y(row.low))); ctx.stroke();
            const bodyTop = y(Math.max(row.open, row.close));
            const bodyBottom = y(Math.min(row.open, row.close));
            const barWidth = Math.max(2, Math.min(14, step * .55));
            ctx.fillRect(px - barWidth / 2, bodyTop, barWidth, Math.max(2, bodyBottom - bodyTop));
            if (!includeWicks && row.high > max) {
                ctx.fillStyle = colors.down; ctx.textAlign = "center"; ctx.fillText("↑", px, top + 10);
            }
            if (!includeWicks && row.low < min) {
                ctx.fillStyle = colors.down; ctx.textAlign = "center"; ctx.fillText("↓", px, priceBottom - 2);
            }
            if (signal) {
                ctx.fillStyle = signal.stage === "走弱" ? "#d77b7b" : signal.stage === "高位整理" ? "#94a7c6" : "#60b392";
                ctx.fillRect(px - Math.max(1, step * .45), top - 1, Math.max(2, step * .9), 3);
            }
            const indicator = indicators.get(row.date);
            if (indicator?.high14 || signal?.new14) {
                const official = Boolean(signal?.new14);
                ctx.strokeStyle = indicator?.high30 || signal?.new30 ? colors.high30 : colors.high14;
                ctx.fillStyle = ctx.strokeStyle;
                ctx.beginPath(); ctx.arc(px, Math.max(top + 15, y(row.close) - 12), indicator?.high30 || signal?.new30 ? 4 : 3, 0, Math.PI * 2);
                if (official) ctx.fill(); else { ctx.globalAlpha = .55; ctx.lineWidth = 1.5; ctx.stroke(); ctx.globalAlpha = 1; }
            }
            if (indicator?.pivot_low) {
                const py = Math.min(priceBottom - 8, y(row.low) + 12);
                ctx.fillStyle = colors.low;
                ctx.beginPath(); ctx.moveTo(px, py - 5); ctx.lineTo(px - 4, py + 2); ctx.lineTo(px + 4, py + 2); ctx.closePath(); ctx.fill();
            }
            if (row.quoteVolume > 0) {
                ctx.fillStyle = row.close >= row.open ? "rgba(22,132,95,.65)" : "rgba(207,91,91,.65)";
                ctx.fillRect(px - barWidth / 2, volumeY(row.quoteVolume), barWidth, volumeBottom - volumeY(row.quoteVolume));
            }
            const highCount = indicator?.recent14_high_count || 0;
            if (highCount) {
                ctx.fillStyle = "rgba(209,138,33,.76)";
                ctx.fillRect(px - barWidth / 2, countY(highCount), barWidth, countBottom - countY(highCount));
            }
            if (index % Math.max(1, Math.ceil(visible.length / (compact ? 5 : 10))) === 0 || index === visible.length - 1) {
                ctx.fillStyle = colors.text; ctx.textAlign = "center"; ctx.fillText(row.date.slice(5), px, height - 15);
            }
        });
        const pivotRows = visible.map((row, index) => ({row, index})).filter(item => indicators.get(item.row.date)?.pivot_low);
        ctx.strokeStyle = colors.low; ctx.lineWidth = 2; ctx.beginPath();
        pivotRows.forEach(({row, index}, pivotIndex) => {
            const py = Math.max(top, Math.min(priceBottom, y(row.low)));
            if (pivotIndex) ctx.lineTo(x(index), py); else ctx.moveTo(x(index), py);
        });
        if (pivotRows.length > 1) ctx.stroke();
        ctx.strokeStyle = colors.center; ctx.lineWidth = 2; ctx.beginPath();
        let started = false;
        visible.forEach((row, index) => {
            const value = Number(indicators.get(row.date)?.center);
            if (!Number.isFinite(value) || value <= 0) { started = false; return; }
            if (!started) ctx.moveTo(x(index), y(value)); else ctx.lineTo(x(index), y(value));
            started = true;
        });
        ctx.stroke();
        if (selectedDate) {
            const index = visible.findIndex(row => row.date === selectedDate);
            if (index >= 0) {
                ctx.strokeStyle = "#63758b"; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
                ctx.beginPath(); ctx.moveTo(x(index), top); ctx.lineTo(x(index), countBottom); ctx.stroke(); ctx.setLineDash([]);
                updateTooltip(visible[index]);
            } else { selectedDate = null; tooltip.hidden = true; }
        } else tooltip.hidden = true;
    }

    if (canvas && rows.length && meta && tooltip) {
        const controls = document.querySelectorAll(".dm-trend-chart-controls [data-range]");
        controls.forEach(button => {
            button.setAttribute("aria-pressed", String(button.dataset.range === range));
            button.addEventListener("click", () => {
                range = button.dataset.range;
                controls.forEach(item => item.setAttribute("aria-pressed", String(item === button)));
                requestDraw();
            });
        });
        const wickButton = document.getElementById("dm-trend-wicks");
        wickButton?.addEventListener("change", () => { includeWicks = wickButton.checked; requestDraw(); });
        const logButton = document.getElementById("dm-trend-log");
        logButton?.addEventListener("change", () => { logScale = logButton.checked; requestDraw(); });
        const pointer = event => {
            if (!geometry) return;
            const rect = canvas.getBoundingClientRect();
            const px = (event.clientX - rect.left) * geometry.width / rect.width;
            const py = (event.clientY - rect.top) * geometry.height / rect.height;
            if (px < geometry.left || px > geometry.right || py < geometry.top || py > geometry.bottom) selectedDate = null;
            else selectedDate = geometry.visible[Math.min(geometry.visible.length - 1, Math.floor((px - geometry.left) / (geometry.right - geometry.left) * geometry.visible.length))].date;
            requestDraw();
        };
        canvas.addEventListener("pointermove", pointer);
        canvas.addEventListener("pointerdown", pointer);
        canvas.addEventListener("pointerleave", event => { if (event.pointerType !== "touch") { selectedDate = null; requestDraw(); } });
        canvas.addEventListener("blur", () => { selectedDate = null; requestDraw(); });
        canvas.addEventListener("keydown", event => {
            if (event.key === "Escape") { selectedDate = null; requestDraw(); return; }
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const visible = activeRows();
            let index = selectedDate ? visible.findIndex(row => row.date === selectedDate) : visible.length - 1;
            if (event.key === "Home") index = 0;
            else if (event.key === "End") index = visible.length - 1;
            else index = Math.max(0, Math.min(visible.length - 1, index + (event.key === "ArrowLeft" ? -1 : 1)));
            selectedDate = visible[index].date; requestDraw();
        });
        new ResizeObserver(requestDraw).observe(canvas);
        draw();
    }
    if (document.querySelector(".dm-trend-spark")) {
        new ResizeObserver(drawSparklines).observe(document.querySelector(".dm-watch-grid"));
        drawSparklines();
    }
})();
