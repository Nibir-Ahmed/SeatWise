"""Search optimization engine for exam hall seat allocation.

Uses two strategies:
1. CSP with backtracking + constraint propagation (small-medium scale)
2. Genetic algorithm (large scale fallback)

All pure Python stdlib — no external solver libraries.
"""

import random
from itertools import product

from models import AllocationResult, Hall, SeatAssignment, Student

# 8-directional adjacency offsets
ADJACENT = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


# --- Constraint Checks ---


def _get_neighbors(
    row: int, col: int, rows: int, cols: int
) -> list[tuple[int, int]]:
    """Return valid adjacent seat coordinates (8-directional)."""
    return [
        (row + dr, col + dc)
        for dr, dc in ADJACENT
        if 0 <= row + dr < rows and 0 <= col + dc < cols
    ]


def _count_conflicts(
    grid: dict[tuple[str, int, int], Student | None],
    halls: list[Hall],
) -> list[str]:
    """Count same-subject adjacency conflicts across all halls."""
    conflicts = []
    for hall in halls:
        for r, c in product(range(hall.rows), range(hall.cols)):
            student = grid.get((hall.id, r, c))
            if not student:
                continue
            for nr, nc in _get_neighbors(r, c, hall.rows, hall.cols):
                neighbor = grid.get((hall.id, nr, nc))
                if neighbor and neighbor.subject == student.subject:
                    pair = tuple(sorted([student.id, neighbor.id]))
                    msg = f"{pair[0]} & {pair[1]} ({student.subject}) adjacent in {hall.name} at ({r},{c})-({nr},{nc})"
                    if msg not in conflicts:
                        conflicts.append(msg)
    return conflicts


def _fitness(
    grid: dict[tuple[str, int, int], Student | None],
    halls: list[Hall],
) -> float:
    """Score an allocation. Higher is better. 100 = perfect (no conflicts)."""
    conflicts = _count_conflicts(grid, halls)
    return max(0.0, 100.0 - len(conflicts) * 5.0)


# --- CSP Solver (Backtracking) ---


def _solve_csp(
    students: list[Student], halls: list[Hall]
) -> dict[tuple[str, int, int], Student | None] | None:
    """Backtracking CSP solver for seat allocation.

    Assigns students to seats ensuring no two adjacent students share a subject.
    Prioritizes accessible seats for students who need them.
    """
    # Build seat list: (hall_id, row, col)
    all_seats: list[tuple[str, int, int]] = []
    accessible_first: list[tuple[str, int, int]] = []
    for hall in halls:
        for r, c in product(range(hall.rows), range(hall.cols)):
            seat = (hall.id, r, c)
            if (r, c) in hall.accessible_seats:
                accessible_first.append(seat)
            else:
                all_seats.append(seat)

    # Sort students: accessible-needing first
    sorted_students = sorted(students, key=lambda s: not s.needs_accessible)

    grid: dict[tuple[str, int, int], Student | None] = {}
    hall_map = {h.id: h for h in halls}

    def is_valid(student: Student, hall_id: str, row: int, col: int) -> bool:
        hall = hall_map[hall_id]
        for nr, nc in _get_neighbors(row, col, hall.rows, hall.cols):
            neighbor = grid.get((hall_id, nr, nc))
            if neighbor and neighbor.subject == student.subject:
                return False
        return True

    def backtrack(idx: int) -> bool:
        if idx == len(sorted_students):
            return True

        student = sorted_students[idx]
        # Pick seat pool based on accessibility need
        seats = accessible_first + all_seats if student.needs_accessible else all_seats + accessible_first

        for hall_id, r, c in seats:
            if (hall_id, r, c) in grid:
                continue
            if is_valid(student, hall_id, r, c):
                grid[(hall_id, r, c)] = student
                if backtrack(idx + 1):
                    return True
                del grid[(hall_id, r, c)]
        return False

    if backtrack(0):
        return grid
    return None


# --- Greedy + Simulated Annealing Solver (Large Scale) ---


def _solve_greedy_sa(
    students: list[Student],
    halls: list[Hall],
    sa_iterations: int = 15000,
) -> dict[tuple[str, int, int], Student | None]:
    """Greedy initial placement + simulated annealing refinement.

    Phase 1 (Greedy): Interleave subjects across seats using a checkerboard-like
    pattern so same-subject students are spread apart.
    Phase 2 (SA): Randomly swap pairs to reduce remaining conflicts, accepting
    worse swaps with decreasing probability (cooling schedule).
    """
    all_seats = [
        (h.id, r, c) for h in halls for r, c in product(range(h.rows), range(h.cols))
    ]

    if len(all_seats) < len(students):
        raise ValueError(
            f"Not enough seats ({len(all_seats)}) for {len(students)} students"
        )

    hall_map = {h.id: h for h in halls}

    # --- Phase 1: Greedy subject-interleaved placement ---
    # Group students by subject, then deal them round-robin across seats
    # sorted in a checkerboard order (maximizes distance between same-subject)
    subjects = sorted(set(s.subject for s in students))
    by_subject: dict[str, list[Student]] = {subj: [] for subj in subjects}
    for s in students:
        by_subject[s.subject].append(s)

    # Interleave: take one from each subject in rotation
    interleaved: list[Student] = []
    queues = {subj: list(reversed(studs)) for subj, studs in by_subject.items()}
    while any(queues.values()):
        for subj in subjects:
            if queues[subj]:
                interleaved.append(queues[subj].pop())

    # Assign interleaved students to seats in order
    grid: dict[tuple[str, int, int], Student | None] = {}
    for i, student in enumerate(interleaved):
        grid[all_seats[i]] = student

    # --- Phase 2: Simulated Annealing refinement ---
    def count_conflicts_for_seat(
        g: dict, hall_id: str, row: int, col: int
    ) -> int:
        student = g.get((hall_id, row, col))
        if not student:
            return 0
        hall = hall_map[hall_id]
        return sum(
            1
            for nr, nc in _get_neighbors(row, col, hall.rows, hall.cols)
            if (n := g.get((hall_id, nr, nc))) and n.subject == student.subject
        )

    def total_conflicts(g: dict) -> int:
        return sum(
            count_conflicts_for_seat(g, hid, r, c) for hid, r, c in g
        )

    import math

    current_cost = total_conflicts(grid)
    keys = list(grid.keys())
    temp = 10.0
    cooling = 0.995

    for _ in range(sa_iterations):
        if current_cost == 0:
            break

        # Pick two random assigned seats and try swapping
        a, b = random.sample(keys, 2)

        # Calculate delta: conflicts before vs after swap
        old = (
            count_conflicts_for_seat(grid, *a)
            + count_conflicts_for_seat(grid, *b)
        )
        grid[a], grid[b] = grid[b], grid[a]
        new = (
            count_conflicts_for_seat(grid, *a)
            + count_conflicts_for_seat(grid, *b)
        )

        delta = new - old
        if delta <= 0 or random.random() < math.exp(-delta / max(temp, 0.01)):
            current_cost += delta  # Accept swap
        else:
            grid[a], grid[b] = grid[b], grid[a]  # Revert

        temp *= cooling

    return grid


# --- Public API ---

SA_THRESHOLD = 30  # Use SA for more than 30 students


def allocate(students: list[Student], halls: list[Hall]) -> AllocationResult:
    """Allocate students to exam hall seats using search optimization.

    Automatically picks CSP (exact) for small instances, SA for large ones.
    """
    total_seats = sum(h.capacity for h in halls)
    if total_seats < len(students):
        raise ValueError(
            f"Not enough seats: {total_seats} available, {len(students)} needed"
        )

    # Pick strategy based on scale
    if len(students) <= SA_THRESHOLD:
        grid = _solve_csp(students, halls)
        if grid is None:
            # CSP failed (too constrained), fall back to SA
            grid = _solve_greedy_sa(students, halls)
    else:
        grid = _solve_greedy_sa(students, halls)

    hall_map = {h.id: h for h in halls}
    conflicts = _count_conflicts(grid, halls)
    score = _fitness(grid, halls)

    assignments = [
        SeatAssignment(
            student_id=student.id,
            student_name=student.name,
            subject=student.subject,
            hall_id=hall_id,
            hall_name=hall_map[hall_id].name,
            row=r,
            col=c,
        )
        for (hall_id, r, c), student in sorted(grid.items())
    ]

    return AllocationResult(
        assignments=assignments,
        total_students=len(students),
        total_seats=total_seats,
        utilization_pct=round(len(students) / total_seats * 100, 1),
        conflicts=conflicts,
        score=score,
    )

