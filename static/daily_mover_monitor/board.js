(() => {
    document.querySelectorAll(".dm-ranking-table").forEach(table => {
        const columns = Array.from(table.querySelectorAll("thead th")).slice(0, 3);
        if (columns.length !== 3) return;
        const update = () => {
            const widths = columns.map(column => column.getBoundingClientRect().width);
            if (!widths[0] || !widths[1]) return;
            table.style.setProperty("--dm-fixed-second", `${widths[0]}px`);
            table.style.setProperty("--dm-fixed-third", `${widths[0] + widths[1]}px`);
        };
        const observer = new ResizeObserver(update);
        columns.forEach(column => observer.observe(column));
        update();
    });
})();
