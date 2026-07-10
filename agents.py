"""AGY SDK agent system for exam hall seat allocation.

Provides an AI-powered conversational interface for:
- Allocating students to seats
- Validating allocations
- Generating reports
- Answering questions about the allocation
"""

import csv
import json
from pathlib import Path

from google.antigravity import Agent, LocalAgentConfig, ToolContext

from models import AllocationResult, Hall, SeatAssignment, Student
from optimizer import allocate, validate


# --- Custom Tools for the Agent ---


def load_students_from_csv(file_path: str) -> str:
    """Load students from a CSV file.

    Args:
        file_path: Path to the CSV file with columns: id, name, subject, department, needs_accessible
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File '{file_path}' not found."

    students = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            students.append(
                Student(
                    id=row["id"],
                    name=row["name"],
                    subject=row["subject"],
                    department=row["department"],
                    needs_accessible=row.get("needs_accessible", "").lower() == "true",
                ).model_dump()
            )

    return json.dumps({"students": students, "count": len(students)})


def load_halls_from_json(file_path: str) -> str:
    """Load exam halls from a JSON file.

    Args:
        file_path: Path to JSON file with hall definitions.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: File '{file_path}' not found."

    with open(path) as f:
        data = json.load(f)

    halls = [Hall(**h) for h in data["halls"]]
    summary = [
        f"  {h.name}: {h.rows}x{h.cols} = {h.capacity} seats ({len(h.accessible_seats)} accessible)"
        for h in halls
    ]
    return f"Loaded {len(halls)} halls:\n" + "\n".join(summary)


def allocate_seats(students_csv: str, halls_json: str, ctx: ToolContext) -> str:
    """Run the seat allocation optimizer.

    Loads students from CSV and halls from JSON, then runs the CSP/GA optimizer
    to find an optimal seat assignment with no same-subject neighbors.

    Args:
        students_csv: Path to students CSV file.
        halls_json: Path to halls JSON file.
        ctx: Tool context for state management.
    """
    # Load students
    csv_path = Path(students_csv)
    if not csv_path.exists():
        return f"Error: Students file '{students_csv}' not found."

    students = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            students.append(
                Student(
                    id=row["id"],
                    name=row["name"],
                    subject=row["subject"],
                    department=row["department"],
                    needs_accessible=row.get("needs_accessible", "").lower() == "true",
                )
            )

    # Load halls
    json_path = Path(halls_json)
    if not json_path.exists():
        return f"Error: Halls file '{halls_json}' not found."

    with open(json_path) as f:
        data = json.load(f)
    halls = [Hall(**h) for h in data["halls"]]

    # Run optimizer
    try:
        result = allocate(students, halls)
    except ValueError as e:
        return f"Allocation failed: {e}"

    # Save result to state
    ctx.set_state("last_allocation", result.model_dump())

    # Format response
    lines = [
        f"✅ Allocation complete!",
        f"   Students: {result.total_students}",
        f"   Seats used: {result.total_students}/{result.total_seats} ({result.utilization_pct}%)",
        f"   Score: {result.score}/100",
        f"   Conflicts: {len(result.conflicts)}",
    ]

    if result.conflicts:
        lines.append("\n⚠️ Conflicts:")
        for c in result.conflicts:
            lines.append(f"   - {c}")

    # Group by hall
    halls_used: dict[str, list[SeatAssignment]] = {}
    for a in result.assignments:
        halls_used.setdefault(a.hall_name, []).append(a)

    lines.append("\n📋 Assignment Summary:")
    for hall_name, assigns in halls_used.items():
        subjects = {}
        for a in assigns:
            subjects[a.subject] = subjects.get(a.subject, 0) + 1
        subject_str = ", ".join(f"{s}: {n}" for s, n in sorted(subjects.items()))
        lines.append(f"   {hall_name}: {len(assigns)} students ({subject_str})")

    return "\n".join(lines)


def validate_allocation(ctx: ToolContext) -> str:
    """Validate the last seat allocation for constraint violations.

    Args:
        ctx: Tool context to retrieve stored allocation.
    """
    data = ctx.get_state("last_allocation")
    if not data:
        return "No allocation found. Run allocate_seats first."

    result = AllocationResult(**data)
    issues = validate(result)

    if not issues:
        return "✅ Allocation is valid! No constraint violations found."

    return f"⚠️ Found {len(issues)} issue(s):\n" + "\n".join(f"  - {i}" for i in issues)


def show_seating_chart(hall_name: str, ctx: ToolContext) -> str:
    """Display a text-based seating chart for a specific hall.

    Args:
        hall_name: Name of the hall to display.
        ctx: Tool context to retrieve stored allocation.
    """
    data = ctx.get_state("last_allocation")
    if not data:
        return "No allocation found. Run allocate_seats first."

    result = AllocationResult(**data)
    hall_assignments = [a for a in result.assignments if a.hall_name == hall_name]

    if not hall_assignments:
        available = set(a.hall_name for a in result.assignments)
        return f"No assignments for '{hall_name}'. Available: {', '.join(available)}"

    # Build grid
    max_row = max(a.row for a in hall_assignments) + 1
    max_col = max(a.col for a in hall_assignments) + 1
    grid: dict[tuple[int, int], SeatAssignment] = {
        (a.row, a.col): a for a in hall_assignments
    }

    # Subject abbreviations (first 3 chars)
    lines = [f"\n🪑 Seating Chart: {hall_name}\n"]
    header = "     " + "  ".join(f"C{c:<3}" for c in range(max_col))
    lines.append(header)
    lines.append("     " + "─" * (max_col * 5))

    for r in range(max_row):
        row_str = f"R{r:<3}│"
        for c in range(max_col):
            a = grid.get((r, c))
            if a:
                row_str += f" {a.subject[:3]:<4}"
            else:
                row_str += "  ·   "
        lines.append(row_str)

    # Legend
    subjects = sorted(set(a.subject for a in hall_assignments))
    lines.append("\n  Legend: " + " | ".join(f"{s[:3]}={s}" for s in subjects))

    return "\n".join(lines)


def get_student_seat(student_name: str, ctx: ToolContext) -> str:
    """Find the seat assignment for a specific student.

    Args:
        student_name: Full or partial name of the student to search for.
        ctx: Tool context to retrieve stored allocation.
    """
    data = ctx.get_state("last_allocation")
    if not data:
        return "No allocation found. Run allocate_seats first."

    result = AllocationResult(**data)
    matches = [
        a
        for a in result.assignments
        if student_name.lower() in a.student_name.lower()
    ]

    if not matches:
        return f"No student matching '{student_name}' found."

    lines = []
    for a in matches:
        lines.append(
            f"📍 {a.student_name} ({a.subject}): {a.hall_name}, Row {a.row}, Col {a.col}"
        )
    return "\n".join(lines)


# --- Agent Factory ---

SYSTEM_PROMPT = """You are an AI Exam Hall Seat Allocation Assistant.

You help administrators allocate students to exam hall seats optimally using
search optimization algorithms (CSP + Genetic Algorithm).

Your capabilities:
1. Load student data from CSV files
2. Load hall configurations from JSON files  
3. Run the seat allocation optimizer (ensures no same-subject students sit adjacent)
4. Validate allocations for constraint violations
5. Display seating charts for specific halls
6. Look up individual student seat assignments

Key constraints enforced:
- No two adjacent students (8-directional) can have the same exam subject
- Students needing accessible seats are prioritized for accessible positions
- Hall capacity limits are respected

Be concise and helpful. Show results clearly. If the user hasn't loaded data yet,
guide them to provide the file paths.
"""


def create_agent_config() -> LocalAgentConfig:
    """Create the AGY agent configuration with all custom tools."""
    return LocalAgentConfig(
        system_instructions=SYSTEM_PROMPT,
        tools=[
            load_students_from_csv,
            load_halls_from_json,
            allocate_seats,
            validate_allocation,
            show_seating_chart,
            get_student_seat,
        ],
    )
