"""
report.py
---------
A command line tool that sits next to the server and does the
"reporting and automation" part of the project:

    python report.py --charts     -> draws matplotlib charts into charts/
    python report.py --summary    -> prints a text summary to the screen
    python report.py --save-report -> writes a dated .txt report into reports/
    python report.py --export-csv -> exports all tickets to exports/

This is meant to be run from a terminal, separately from the server,
which is why it uses argparse for its command line arguments.
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")  # so it can run without a screen attached
import matplotlib.pyplot as plt

from database import init_db
import utils

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHARTS_DIR = os.path.join(BASE_DIR, "charts")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

# A small set of colours that feel Nigerian (green/white) without
# being a plain default matplotlib theme.
BRAND_GREEN = "#0F8B4C"
BRAND_GOLD = "#E4A63A"
BRAND_DARK = "#123524"


def make_charts_dir():
    os.makedirs(CHARTS_DIR, exist_ok=True)


def draw_tickets_by_event_chart(stats):
    """Bar chart: number of tickets sold per event (top 5)."""
    top = stats["top_events"]
    if not top:
        return
    names = [t["title"][:18] for t in top]
    sold = [t["sold"] for t in top]

    plt.figure(figsize=(7, 4.5))
    plt.bar(names, sold, color=BRAND_GREEN)
    plt.title("Tickets Sold by Event (Top 5)", fontsize=13, fontweight="bold", color=BRAND_DARK)
    plt.ylabel("Tickets sold")
    plt.xticks(rotation=25, ha="right")
    plt.tight_layout()
    plt.savefig(os.path.join(CHARTS_DIR, "tickets_by_event.png"), dpi=140)
    plt.close()


def draw_events_by_category_chart(stats):
    """Pie chart: how events are spread across categories."""
    by_cat = stats["by_category"]
    if not by_cat:
        return
    labels = [c["category"] for c in by_cat]
    sizes = [c["total"] for c in by_cat]
    colors = [BRAND_GREEN, BRAND_GOLD, "#1F6F5C", "#C9762C", "#2D9C6B",
              "#8C5A2B", "#3AAE8C", "#D98E3F"][: len(labels)]

    plt.figure(figsize=(6, 6))
    plt.pie(sizes, labels=labels, autopct="%1.0f%%", colors=colors,
            wedgeprops={"edgecolor": "white", "linewidth": 1.5})
    plt.title("Events by Category", fontsize=13, fontweight="bold", color=BRAND_DARK)
    plt.tight_layout()
    plt.savefig(os.path.join(CHARTS_DIR, "events_by_category.png"), dpi=140)
    plt.close()


def draw_overview_chart(stats):
    """Simple horizontal bar comparing totals across the whole platform."""
    labels = ["Events", "Tickets Sold", "Registered Users"]
    values = [stats["total_events"], stats["total_tickets"], stats["total_users"]]

    plt.figure(figsize=(6.5, 3.5))
    plt.barh(labels, values, color=[BRAND_GREEN, BRAND_GOLD, BRAND_DARK])
    plt.title("Platform Overview", fontsize=13, fontweight="bold", color=BRAND_DARK)
    for i, v in enumerate(values):
        plt.text(v, i, f"  {v}", va="center", fontweight="bold")
    plt.tight_layout()
    plt.savefig(os.path.join(CHARTS_DIR, "platform_overview.png"), dpi=140)
    plt.close()


def build_charts():
    make_charts_dir()
    stats = utils.get_statistics()
    draw_tickets_by_event_chart(stats)
    draw_events_by_category_chart(stats)
    draw_overview_chart(stats)
    print(f"Charts saved to: {CHARTS_DIR}")


def print_summary():
    stats = utils.get_statistics()
    print("\n=== NAIJA EVENTS - SUMMARY ===")
    print(f"Total events        : {stats['total_events']}")
    print(f"Total tickets sold   : {stats['total_tickets']}")
    print(f"Registered users     : {stats['total_users']}")
    print(f"Estimated revenue    : NGN {stats['total_revenue']:,.2f}")
    print("\nTop events by tickets sold:")
    for ev in stats["top_events"]:
        print(f"  - {ev['title']}: {ev['sold']} tickets")
    print("\nEvents by category:")
    for cat in stats["by_category"]:
        print(f"  - {cat['category']}: {cat['total']} event(s)")
    print()


def save_report():
    """Write a dated plain-text report file into reports/."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    stats = utils.get_statistics()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    path = os.path.join(REPORTS_DIR, filename)

    lines = []
    lines.append("NAIJA EVENTS - AUTOMATED REPORT")
    lines.append(f"Generated: {stamp}")
    lines.append("-" * 40)
    lines.append(f"Total events: {stats['total_events']}")
    lines.append(f"Total tickets sold: {stats['total_tickets']}")
    lines.append(f"Registered users: {stats['total_users']}")
    lines.append(f"Estimated revenue: NGN {stats['total_revenue']:,.2f}")
    lines.append("")
    lines.append("Top events by tickets sold:")
    for ev in stats["top_events"]:
        lines.append(f"  - {ev['title']}: {ev['sold']} tickets")
    lines.append("")
    lines.append("Events by category:")
    for cat in stats["by_category"]:
        lines.append(f"  - {cat['category']}: {cat['total']} event(s)")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Report saved to: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Naija Events reporting tool - run this from the terminal."
    )
    parser.add_argument("--charts", action="store_true", help="Draw matplotlib charts into charts/")
    parser.add_argument("--summary", action="store_true", help="Print a text summary to the screen")
    parser.add_argument("--save-report", action="store_true", help="Save a dated .txt report into reports/")
    parser.add_argument("--export-csv", action="store_true", help="Export all tickets to a CSV file")
    args = parser.parse_args()

    init_db()  # make sure tables exist even if the server was never started

    if not any([args.charts, args.summary, args.save_report, args.export_csv]):
        parser.print_help()
        return

    if args.summary:
        print_summary()
    if args.charts:
        build_charts()
    if args.save_report:
        save_report()
    if args.export_csv:
        path = utils.export_tickets_to_csv()
        print(f"Tickets exported to: {path}")


if __name__ == "__main__":
    main()
