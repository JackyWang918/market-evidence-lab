"""Read-only overlays derived from retained daily candles for the chart."""

from decimal import Decimal
from statistics import median


def chart_indicators(candles):
    rows = [{"date": row["date"], "close": Decimal(row["close"]), "low": Decimal(row["low"])}
            for row in candles]
    overlay = []
    high_flags = []
    for index, row in enumerate(rows):
        prior14 = rows[max(0, index - 14):index]
        prior30 = rows[max(0, index - 30):index]
        pivot_low = (
            index >= 2 and index + 2 < len(rows)
            and all(row["low"] < rows[neighbor]["low"]
                    for neighbor in (index - 2, index - 1, index + 1, index + 2))
        )
        high14 = len(prior14) == 14 and row["close"] > max(item["close"] for item in prior14)
        high_flags.append(high14)
        overlay.append({
            "date": row["date"],
            "center": str(median(item["close"] for item in rows[index - 9:index + 1])) if index >= 9 else None,
            "high14": high14,
            "high30": len(prior30) == 30 and row["close"] > max(item["close"] for item in prior30),
            "pivot_low": pivot_low,
            "recent14_high_count": sum(high_flags[-14:]),
        })
    return overlay
