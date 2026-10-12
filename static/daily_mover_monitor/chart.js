(() => {
    const canvas = document.getElementById("dm-candles");
    const source = document.getElementById("dm-candle-data");
    const meta = document.getElementById("dm-chart-meta");
    const tooltip = document.getElementById("dm-chart-tooltip");
    if (!canvas || !source || !meta || !tooltip) return;
    const rows = JSON.parse(source.textContent).map((item) => ({
        ...item,
        raw: item,
        open: Number(item.open), high: Number(item.high),
        low: Number(item.low), close: Number(item.close),
    }));
    if (!rows.length) return;
    let geometry = null;
    let hover = null;
    let frame = null;
    const title = document.createElement("strong");
    const facts = document.createElement("dl");
    const values = {};
    [["open", "开盘"], ["high", "最高"], ["low", "最低"], ["close", "收盘"], ["change", "当日涨跌"]].forEach(([key, label]) => {
        const line = document.createElement("div");
        const term = document.createElement("dt");
        const value = document.createElement("dd");
        term.textContent = label;
        line.append(term, value);
        facts.append(line);
        values[key] = value;
    });
    tooltip.append(title, facts);
    const precision = Math.max(4, Math.min(10, Math.max(...rows.map(row =>
        (String(row.raw.close).split(".")[1] || "").replace(/0+$/, "").length))));
    const priceText = value => value.toFixed(precision).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");
    const rawText = value => String(value).replace(/(\.\d*?[1-9])0+$|\.0+$/, "$1");

    function requestDraw() {
        if (frame !== null) return;
        frame = requestAnimationFrame(() => { frame = null; draw(); });
    }

    function draw() {
        const width = canvas.clientWidth;
        if (!width) return;
        const compact = width < 520;
        const height = compact ? 280 : 350;
        canvas.style.height = `${height}px`;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        const ctx = canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        ctx.font = "12px system-ui, sans-serif";
        const left = Math.max(compact ? 64 : 80, ctx.measureText(priceText(Math.max(...rows.map(row => row.high)))).width + 14);
        const right = width - (compact ? 8 : 18);
        const top = 24;
        const bottom = height - 38;
        const min = Math.min(...rows.map((row) => row.low)) * .94;
        const max = Math.max(...rows.map((row) => row.high)) * 1.06;
        const y = (value) => bottom - (value - min) / (max - min) * (bottom - top);
        const x = (index) => left + (index + .5) * (right - left) / rows.length;
        geometry = {width, height, left, right, top, bottom, min, max};
        ctx.font = "12px system-ui, sans-serif";
        ctx.lineWidth = 1;
        for (let tick = 0; tick <= 4; tick++) {
            const value = min + (max - min) * tick / 4;
            const py = y(value);
            ctx.strokeStyle = "#e8edf4";
            ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke();
            ctx.fillStyle = "#8492a6";
            ctx.textAlign = "right";
            ctx.fillText(value.toPrecision(compact ? 3 : 4), left - 7, py + 4);
        }
        rows.forEach((row, index) => {
            const px = x(index);
            const color = row.close >= row.open ? "#23956d" : "#d96565";
            ctx.strokeStyle = color;
            ctx.fillStyle = color;
            ctx.lineWidth = 2;
            ctx.beginPath(); ctx.moveTo(px, y(row.high)); ctx.lineTo(px, y(row.low)); ctx.stroke();
            const bodyTop = Math.min(y(row.open), y(row.close));
            const bodyHeight = Math.max(2, Math.abs(y(row.open) - y(row.close)));
            const bodyWidth = Math.min(22, (right - left) / rows.length * .45);
            ctx.fillRect(px - bodyWidth / 2, bodyTop, bodyWidth, bodyHeight);
            ctx.fillStyle = "#8a97a8";
            ctx.textAlign = "center";
            ctx.font = "11px system-ui, sans-serif";
            if (!compact || index % 2 === 0 || index === rows.length - 1) {
                if (index % Math.max(1, Math.ceil(rows.length / (compact ? 6 : 12))) === 0 || index === rows.length - 1) ctx.fillText(row.time.slice(5), px, height - 13);
            }
            if (row.time === meta.dataset.low || row.time === meta.dataset.high) {
                const isLow = row.time === meta.dataset.low;
                const markerY = isLow ? y(row.low) + 20 : y(row.high) - 12;
                ctx.fillStyle = isLow ? "#315bce" : "#d68a2b";
                ctx.font = "bold 12px system-ui, sans-serif";
                ctx.fillText(isLow ? "低点" : "高点", px, markerY);
            }
        });
        const confirmed = rows.findIndex(row => row.time === meta.dataset.confirmed);
        if (confirmed >= 0) {
            [[meta.dataset.platformLow, "平台下沿"], [meta.dataset.platformHigh, "平台上沿"]].forEach(([price, label]) => {
                const value = Number(price);
                if (!Number.isFinite(value) || value <= 0) return;
                ctx.strokeStyle = "#315bce";
                ctx.lineWidth = 1;
                ctx.setLineDash([5, 4]);
                ctx.beginPath(); ctx.moveTo(x(confirmed), y(value)); ctx.lineTo(right, y(value)); ctx.stroke();
                ctx.setLineDash([]);
                ctx.fillStyle = "#315bce"; ctx.textAlign = "right";
                ctx.fillText(`${label} ${value.toPrecision(5)}`, right - 4, y(value) - 5);
            });
        }
        if (!hover) {
            tooltip.hidden = true;
            return;
        }
        const selected = rows[hover.index];
        const px = x(hover.index);
        const py = top + hover.fractionY * (bottom-top);
        const pointerPrice = max - hover.fractionY * (max-min);
        ctx.strokeStyle = "#62738c";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(left, py); ctx.lineTo(right, py);
        ctx.moveTo(px, top); ctx.lineTo(px, bottom);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.font = "12px system-ui, sans-serif";
        const label = priceText(pointerPrice);
        const badgeWidth = ctx.measureText(label).width + 12;
        const badgeY = Math.max(top, Math.min(bottom-22, py-11));
        ctx.fillStyle = "#253b59";
        ctx.fillRect(Math.max(0,left-badgeWidth), badgeY, badgeWidth, 22);
        ctx.fillStyle = "#fff";
        ctx.textAlign = "right";
        ctx.fillText(label, left-6, badgeY+15);
        const dateWidth = ctx.measureText(selected.time).width+12;
        const dateLeft = Math.max(left, Math.min(right-dateWidth, px-dateWidth/2));
        ctx.fillStyle = "#253b59";
        ctx.fillRect(dateLeft, bottom+10, dateWidth, 22);
        ctx.fillStyle = "#fff";
        ctx.textAlign = "center";
        ctx.fillText(selected.time, dateLeft+dateWidth/2, bottom+25);
        title.textContent = `${selected.time} UTC`;
        ["open", "high", "low", "close"].forEach(key => { values[key].textContent = rawText(selected.raw[key]); });
        const change = (selected.close / selected.open - 1) * 100;
        values.change.textContent = `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
        values.change.className = change >= 0 ? "dm-positive" : "dm-negative";
        tooltip.hidden = false;
        // Keep the card inside the chart even at the first/last candle or on phones.
        const cardWidth = tooltip.offsetWidth;
        const cardHeight = tooltip.offsetHeight;
        tooltip.style.left = `${Math.max(0, Math.min(width-cardWidth, px+18+cardWidth <= width ? px+18 : px-cardWidth-18))}px`;
        tooltip.style.top = `${Math.max(0, Math.min(height-cardHeight, py+16))}px`;
    }
    function pointer(event) {
        if (!geometry) return;
        const rect = canvas.getBoundingClientRect();
        const px = (event.clientX-rect.left)*geometry.width/rect.width;
        const py = (event.clientY-rect.top)*geometry.height/rect.height;
        const {left, right, top, bottom} = geometry;
        if (px < left || px > right || py < top || py > bottom) {
            hover = null;
        } else {
            hover = {index: Math.min(rows.length-1, Math.floor((px-left)/(right-left)*rows.length)),
                     fractionY: (py-top)/(bottom-top)};
        }
        requestDraw();
    }
    canvas.addEventListener("pointermove", pointer);
    canvas.addEventListener("pointerdown", pointer);
    canvas.addEventListener("pointerleave", event => {
        if (event.pointerType !== "touch") { hover = null; requestDraw(); }
    });
    canvas.addEventListener("blur", () => { hover = null; requestDraw(); });
    canvas.addEventListener("keydown", event => {
        if (event.key === "Escape") { hover = null; requestDraw(); return; }
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key) || !geometry) return;
        event.preventDefault();
        let index = hover ? hover.index : rows.length-1;
        if (event.key === "Home") index = 0;
        else if (event.key === "End") index = rows.length-1;
        else index = Math.max(0, Math.min(rows.length-1, index + (event.key === "ArrowLeft" ? -1 : 1)));
        hover = {index, fractionY: Math.max(0, Math.min(1, (geometry.max-rows[index].close)/(geometry.max-geometry.min)))};
        requestDraw();
    });
    new ResizeObserver(requestDraw).observe(canvas);
    draw();
})();
