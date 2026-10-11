(() => {
    const canvas = document.getElementById("dm-candles");
    const source = document.getElementById("dm-candle-data");
    const meta = document.getElementById("dm-chart-meta");
    if (!canvas || !source || !meta) return;
    const rows = JSON.parse(source.textContent).map((item) => ({
        ...item,
        open: Number(item.open), high: Number(item.high),
        low: Number(item.low), close: Number(item.close),
    }));
    if (!rows.length) return;

    function draw() {
        const width = canvas.clientWidth;
        if (!width) return;
        const compact = width < 520;
        const height = compact ? 280 : 350;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(height * ratio);
        const ctx = canvas.getContext("2d");
        ctx.scale(ratio, ratio);
        const left = compact ? 45 : 64;
        const right = width - (compact ? 8 : 18);
        const top = 24;
        const bottom = height - 38;
        const min = Math.min(...rows.map((row) => row.low)) * .94;
        const max = Math.max(...rows.map((row) => row.high)) * 1.06;
        const y = (value) => bottom - (value - min) / (max - min) * (bottom - top);
        const x = (index) => left + (index + .5) * (right - left) / rows.length;
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
                ctx.fillText(row.time.slice(5), px, height - 13);
            }
            if (row.time === meta.dataset.low || row.time === meta.dataset.high) {
                const isLow = row.time === meta.dataset.low;
                const markerY = isLow ? y(row.low) + 20 : y(row.high) - 12;
                ctx.fillStyle = isLow ? "#315bce" : "#d68a2b";
                ctx.font = "bold 12px system-ui, sans-serif";
                ctx.fillText(isLow ? "低点" : "高点", px, markerY);
            }
        });
    }
    new ResizeObserver(draw).observe(canvas);
    draw();
})();
