"""CLI entry point for Exam Hall Seat Allocation.

Loads CSV/JSON → allocates → prints results using search optimization.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from models import Hall, Student
from optimizer import allocate

console = Console()


def print_banner():
    console.print(
        Panel.fit(
            "[bold cyan]Exam Hall Seat Allocation[/]\n"
            "[dim]Search Optimization · CSP + Simulated Annealing[/]",
            border_style="cyan",
        )
    )


# --- Auto Mode ---


def run_auto(students_path: str, halls_path: str):
    """Load data, run optimizer, print results."""
    console.print("\n[bold]Loading data...[/]")

    # Load students
    students = []
    with open(students_path) as f:
        for row in csv.DictReader(f):
            students.append(
                Student(
                    id=row["id"],
                    name=row["name"],
                    subject=row["subject"],
                    department=row["department"],
                    needs_accessible=row.get("needs_accessible", "").lower() == "true",
                )
            )
    console.print(f"  {len(students)} students loaded successfully")

    # Load halls
    with open(halls_path) as f:
        data = json.load(f)
    halls = [Hall(**h) for h in data["halls"]]
    total_seats = sum(h.capacity for h in halls)
    console.print(f"  {len(halls)} halls loaded successfully ({total_seats} total seats)")

    # Run optimizer
    console.print("\n[bold]Running optimizer...[/]")
    with console.status("[cyan]Searching for optimal allocation..."):
        result = allocate(students, halls)

    # Score panel
    score_color = "green" if result.score >= 90 else "yellow" if result.score >= 70 else "red"
    console.print(
        Panel(
            f"[bold {score_color}]Score: {result.score}/100[/]\n"
            f"Students: {result.total_students}\n"
            f"Utilization: {result.utilization_pct}%\n"
            f"Conflicts: {len(result.conflicts)}",
            title="[bold]Allocation Result[/]",
            border_style=score_color,
        )
    )

    # Assignments table per hall
    halls_used: dict[str, list] = {}
    for a in result.assignments:
        halls_used.setdefault(a.hall_name, []).append(a)

    for hall_name, assigns in halls_used.items():
        table = Table(title=f"Hall: {hall_name}", show_lines=True)
        table.add_column("Student", style="cyan")
        table.add_column("Subject", style="magenta")
        table.add_column("Row", justify="center")
        table.add_column("Col", justify="center")

        for a in sorted(assigns, key=lambda x: (x.row, x.col)):
            table.add_row(a.student_name, a.subject, str(a.row), str(a.col))

        console.print(table)

    # Seating grid visualization
    for hall_name, assigns in halls_used.items():
        max_row = max(a.row for a in assigns) + 1
        max_col = max(a.col for a in assigns) + 1
        grid = {(a.row, a.col): a for a in assigns}

        console.print(f"\n[bold]Seating Grid: {hall_name}[/]")
        header = "      " + "  ".join(f"[dim]C{c}[/]  " for c in range(max_col))
        console.print(header)

        subject_colors = {}
        color_cycle = ["red", "green", "blue", "yellow", "magenta", "cyan"]
        for a in assigns:
            if a.subject not in subject_colors:
                subject_colors[a.subject] = color_cycle[len(subject_colors) % len(color_cycle)]

        for r in range(max_row):
            row_str = f"[dim]R{r}[/]  │"
            for c in range(max_col):
                a = grid.get((r, c))
                if a:
                    color = subject_colors[a.subject]
                    row_str += f" [{color}]{a.subject[:3]:>3}[/] "
                else:
                    row_str += " [dim] ·  [/]"
            console.print(row_str)

        # Legend
        legend = "  ".join(
            f"[{color}]■ {subj}[/]" for subj, color in subject_colors.items()
        )
        console.print(f"\n  {legend}")

    # Conflicts
    if result.conflicts:
        console.print(f"\n[bold red]Conflicts ({len(result.conflicts)} remaining):[/]")
        for c in result.conflicts:
            console.print(f"  [red]- {c}[/]")
    else:
        console.print("\n[bold green]No conflicts! Perfect allocation.[/]")


# --- CLI ---


def cli():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Exam Hall Seat Allocation — Search Optimization"
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # Auto mode
    auto = sub.add_parser("auto", help="Run allocation from files")
    auto.add_argument("--students", "-s", required=True, help="Path to students CSV")
    auto.add_argument("--halls", "-H", required=True, help="Path to halls JSON")

    args = parser.parse_args()

    print_banner()

    if args.mode == "auto":
        run_auto(args.students, args.halls)


if __name__ == "__main__":
    cli()
