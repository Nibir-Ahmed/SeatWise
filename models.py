"""Data models for exam hall seat allocation."""

from pydantic import BaseModel


class Student(BaseModel):
    id: str
    name: str
    subject: str
    department: str
    needs_accessible: bool = False


class Seat(BaseModel):
    row: int
    col: int
    is_accessible: bool = False


class Hall(BaseModel):
    id: str
    name: str
    rows: int
    cols: int
    accessible_seats: list[tuple[int, int]] = []

    @property
    def capacity(self) -> int:
        return self.rows * self.cols

    def seat_at(self, row: int, col: int) -> Seat:
        return Seat(
            row=row,
            col=col,
            is_accessible=(row, col) in self.accessible_seats,
        )


class SeatAssignment(BaseModel):
    student_id: str
    student_name: str
    subject: str
    hall_id: str
    hall_name: str
    row: int
    col: int


class AllocationResult(BaseModel):
    assignments: list[SeatAssignment]
    total_students: int
    total_seats: int
    utilization_pct: float
    conflicts: list[str]
    score: float
