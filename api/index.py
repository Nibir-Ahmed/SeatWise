"""Flask Web Server for Exam Seat Finder (Vercel Entry Point).

Provides web routes and API endpoints for:
- Dashboard display of upcoming exams
- Step-by-step seat allocation wizard
- Seating map visualizer with manual swaps and conflict checking
- Dynamic re-optimization and PDF export layout
"""

import csv
import json
import os
import sys
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify
from groq import Groq

# Ensure project root is in python path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from models import Hall, Student, SeatAssignment, AllocationResult
from optimizer import allocate, validate

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.secret_key = "seatwise_secret_key"

# Paths
if "VERCEL" in os.environ:
    DATA_DIR = Path("/tmp")
else:
    DATA_DIR = BASE_DIR / "data"
    DATA_DIR.mkdir(exist_ok=True)

EXAMS_JSON = DATA_DIR / "active_exams.json"
ALLOCATION_JSON = DATA_DIR / "active_allocation.json"
DEFAULT_STUDENTS_CSV = BASE_DIR / "data" / "sample_students.csv"
DEFAULT_HALLS_JSON = BASE_DIR / "data" / "sample_halls.json"


# --- Helper Functions ---

def load_exams():
    """Load upcoming exams from JSON database."""
    if not EXAMS_JSON.exists():
        save_exams([])
        return []
    
    with open(EXAMS_JSON) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_exams(exams):
    """Save exams to JSON database."""
    with open(EXAMS_JSON, "w") as f:
        json.dump(exams, f, indent=2)


def get_default_halls():
    """Load default halls list from sample JSON."""
    with open(DEFAULT_HALLS_JSON) as f:
        data = json.load(f)
    return [Hall(**h) for h in data["halls"]]


def get_default_students():
    """Load default students list from sample CSV."""
    students = []
    with open(DEFAULT_STUDENTS_CSV) as f:
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
    return students


def ensure_active_allocation():
    """Generate default active allocation if none exists."""
    if not ALLOCATION_JSON.exists():
        students = get_default_students()
        halls = get_default_halls()
        result = allocate(students, halls)
        
        alloc_data = result.model_dump()
        alloc_data["exam_title"] = "End-Sem Semester 3"
        alloc_data["exam_date"] = "2026-11-24"
        alloc_data["exam_session"] = "Morning - 9:30 AM"
        alloc_data["exam_duration"] = "3 hours"
        alloc_data["_students"] = [s.model_dump() for s in students]
        alloc_data["_halls"] = [h.model_dump() for h in halls]
        save_allocation(alloc_data)


def load_allocation():
    """Load current active allocation result from file."""
    ensure_active_allocation()
    with open(ALLOCATION_JSON) as f:
        data = json.load(f)
    return data


def save_allocation(result):
    """Save allocation result to file."""
    with open(ALLOCATION_JSON, "w") as f:
        # Pydantic model dump to JSON
        if isinstance(result, AllocationResult):
            f.write(result.model_dump_json(indent=2))
        else:
            json.dump(result, f, indent=2)


# --- Routes ---

@app.route("/")
def index():
    """Dashboard page."""
    exams = load_exams()
    return render_template("dashboard.html", exams=exams)


@app.route("/new")
def new_allocation():
    """Wizard setup for new allocation."""
    return render_template("new_allocation.html")


@app.route("/seat-map")
def seat_map():
    """Interactive seating grid display."""
    allocation = load_allocation()
    
    # Extract unique halls from the assignments to display on left sidebar
    halls_dict = {}
    for a in allocation.get("assignments", []):
        hall_id = a["hall_id"]
        if hall_id not in halls_dict:
            halls_dict[hall_id] = {
                "id": hall_id,
                "name": a["hall_name"],
                "rows": 0,
                "cols": 0,
                "accessible_seats": []
            }
            
    # Resolve exact row/col sizes and accessibility from default json
    try:
        all_halls = get_default_halls()
        for h in all_halls:
            if h.id in halls_dict:
                halls_dict[h.id]["rows"] = h.rows
                halls_dict[h.id]["cols"] = h.cols
                halls_dict[h.id]["accessible_seats"] = h.accessible_seats
    except Exception:
        pass
    
    # If halls dict is empty (no assignments), load default halls
    if not halls_dict:
        halls = [h.model_dump() for h in get_default_halls()]
    else:
        halls = list(halls_dict.values())
        
    return render_template("seat_map.html", allocation=allocation, halls=halls)


@app.route("/api/allocate", methods=["POST"])
def api_allocate():
    """Parse uploads, run optimizer, and save allocation."""
    title = request.form.get("exam_title", "New Exam")
    date = request.form.get("exam_date", "2026-11-24")
    session = request.form.get("exam_session", "Morning - 9:30 AM")
    duration = request.form.get("exam_duration", "3 hours")
    selected_hall_ids = request.form.getlist("selected_halls")

    # Load students
    students = []
    students_file = request.files.get("students_csv")
    if students_file and students_file.filename != "":
        # Process custom CSV
        stream = students_file.stream.read().decode("utf-8-sig").splitlines()
        reader = csv.DictReader(stream)
        
        # Helper to find column name by aliases
        def find_col(row_dict, aliases):
            for k in row_dict.keys():
                if k and k.strip().lower() in aliases:
                    return k
            return None

        id_aliases = {"id", "student id", "student_id", "roll", "roll no", "roll no.", "roll number", "reg", "registration no", "registration number"}
        name_aliases = {"name", "student name", "student_name", "full name", "fullname"}
        sub_aliases = {"subject", "course", "course code", "course_code", "exam", "paper"}
        dept_aliases = {"department", "dept", "program"}
        acc_aliases = {"needs_accessible", "needs_accessibility", "accessible", "disability", "special needs", "accessibility"}

        for row in reader:
            id_key = find_col(row, id_aliases)
            name_key = find_col(row, name_aliases)
            sub_key = find_col(row, sub_aliases)
            dept_key = find_col(row, dept_aliases)
            acc_key = find_col(row, acc_aliases)

            if id_key and name_key and sub_key:
                val_id = row[id_key].strip()
                val_name = row[name_key].strip()
                val_sub = row[sub_key].strip()
                val_dept = row[dept_key].strip() if (dept_key and row[dept_key]) else "General"
                
                val_acc = False
                if acc_key and row[acc_key]:
                    val_acc = row[acc_key].strip().lower() in ("true", "yes", "1", "y")

                if val_id and val_name and val_sub:
                    students.append(
                        Student(
                            id=val_id,
                            name=val_name,
                            subject=val_sub,
                            department=val_dept,
                            needs_accessible=val_acc
                        )
                    )
        
        if not students:
            return jsonify({
                "success": False, 
                "error": "Could not parse any students from the uploaded CSV. Please check that your CSV has headers for 'id' (or 'roll'), 'name', and 'subject'."
            }), 400
    else:
        # Fall back to default roster
        students = get_default_students()

    # Load halls
    halls = []
    halls_file = request.files.get("halls_json")
    if halls_file and halls_file.filename != "":
        # Process custom JSON
        data = json.load(halls_file)
        halls = [Hall(**h) for h in data.get("halls", [])]
    else:
        # Fall back to default halls
        halls = get_default_halls()

    # Filter halls to only those selected
    if selected_hall_ids:
        halls = [h for h in halls if h.id in selected_hall_ids]
    
    if not halls:
        halls = get_default_halls()

    # Run allocation optimizer
    try:
        result = allocate(students, halls)
    except ValueError as e:
        return f"Optimization failed: {e}. Not enough seats.", 400

    # Save allocation
    save_allocation(result)

    # Save custom students & halls for re-optimization or swapping references
    alloc_data = result.model_dump()
    alloc_data["exam_title"] = title
    alloc_data["exam_date"] = date
    alloc_data["exam_session"] = session
    alloc_data["exam_duration"] = duration
    alloc_data["_students"] = [s.model_dump() for s in students]
    alloc_data["_halls"] = [h.model_dump() for h in halls]
    save_allocation(alloc_data)

    # Add new exam to active_exams.json list
    exams = load_exams()
    new_exam = {
        "id": f"exam_{len(exams) + 1:03d}",
        "title": title,
        "date": date,
        "session": session,
        "duration": duration,
        "status": "Ready" if len(result.conflicts) == 0 else "Optimizing",
        "students_count": len(students),
        "halls_count": len(halls),
        "conflicts_count": len(result.conflicts),
        "score": result.score / 100.0
    }
    
    # Remove older versions with same title to prevent duplication
    exams = [e for e in exams if e["title"] != title]
    exams.insert(0, new_exam)
    save_exams(exams)

    return redirect(url_for("seat_map"))


@app.route("/api/reoptimize", methods=["POST"])
def api_reoptimize():
    """Trigger Simulated Annealing with extra iterations to resolve conflicts."""
    allocation = load_allocation()
    
    # Retrieve saved students and halls lists
    students_data = allocation.get("_students")
    halls_data = allocation.get("_halls")
    
    if students_data and halls_data:
        students = [Student(**s) for s in students_data]
        halls = [Hall(**h) for h in halls_data]
    else:
        students = get_default_students()
        halls = get_default_halls()
        
    # Re-run optimizer with shuffling
    import random
    random.shuffle(students)
    
    try:
        result = allocate(students, halls)
    except ValueError:
        return redirect(url_for("seat_map"))
        
    # Save allocation with metadata
    alloc_data = result.model_dump()
    alloc_data["_students"] = [s.model_dump() for s in students]
    alloc_data["_halls"] = [h.model_dump() for h in halls]
    save_allocation(alloc_data)
    
    # Update active exam status in dashboard
    exams = load_exams()
    if exams:
        exams[0]["conflicts_count"] = len(result.conflicts)
        exams[0]["status"] = "Ready" if len(result.conflicts) == 0 else "Optimizing"
        exams[0]["score"] = result.score / 100.0
        save_exams(exams)
        
    return redirect(url_for("seat_map"))


@app.route("/api/swap", methods=["POST"])
def api_swap():
    """Manually swap seats of two students and re-validate constraints."""
    student1_id = request.form.get("student1_id")
    student2_id = request.form.get("student2_id")
    
    allocation = load_allocation()
    assignments = allocation.get("assignments", [])
    
    # Find matching assignments
    assign1_idx = next((i for i, a in enumerate(assignments) if a["student_id"] == student1_id), None)
    assign2_idx = next((i for i, a in enumerate(assignments) if a["student_id"] == student2_id), None)
    
    if assign1_idx is not None and assign2_idx is not None:
        # Swap seat locations (hall, row, col)
        a1 = assignments[assign1_idx]
        a2 = assignments[assign2_idx]
        
        # Temp copy of seat info
        hall_id_temp, hall_name_temp = a1["hall_id"], a1["hall_name"]
        row_temp, col_temp = a1["row"], a1["col"]
        
        # Assign 2's seat info to 1
        a1["hall_id"], a1["hall_name"] = a2["hall_id"], a2["hall_name"]
        a1["row"], a1["col"] = a2["row"], a2["col"]
        
        # Assign 1's seat info to 2
        a2["hall_id"], a2["hall_name"] = hall_id_temp, hall_name_temp
        a2["row"], a2["col"] = row_temp, col_temp
        
        # Re-validate conflicts
        halls_data = allocation.get("_halls")
        if halls_data:
            halls = [Hall(**h) for h in halls_data]
        else:
            halls = get_default_halls()
            
        # Reconstruct grid map for validation
        grid = {}
        students_map = {}
        students_data = allocation.get("_students", [])
        for s in students_data:
            students_map[s["id"]] = Student(**s)
            
        for a in assignments:
            s_obj = students_map.get(a["student_id"])
            if s_obj:
                grid[(a["hall_id"], a["row"], a["col"])] = s_obj
                
        # Count conflicts using optimizer's check functions
        from optimizer import _count_conflicts, _fitness
        conflicts = _count_conflicts(grid, halls)
        score = _fitness(grid, halls)
        
        # Update allocation fields
        allocation["assignments"] = assignments
        allocation["conflicts"] = conflicts
        allocation["score"] = score
        
        save_allocation(allocation)
        
        # Update dashboard active exam stats
        exams = load_exams()
        if exams:
            exams[0]["conflicts_count"] = len(conflicts)
            exams[0]["status"] = "Ready" if len(conflicts) == 0 else "Optimizing"
            exams[0]["score"] = score / 100.0
            save_exams(exams)
            
    return redirect(url_for("seat_map"))


@app.route("/api/ai_chat", methods=["POST"])
def api_ai_chat():
    """Contact Groq API to query recommendations or chat about the seating layout."""
    user_message = request.json.get("message", "")
    
    # Load current allocation state
    try:
        allocation = load_allocation()
        total_students = len(allocation.get("assignments", []))
        score = allocation.get("score", 0)
        conflicts = allocation.get("conflicts", [])
    except Exception:
        total_students = 0
        score = 0
        conflicts = []
        
    system_prompt = f"""You are the Seatwise AI Allocation Copilot. 
You help exam cell administrators optimize seating grids, resolve conflicts, and make swap decisions.
Current Layout State:
- Allocated Students: {total_students}
- Current Layout Score: {score}/100
- Conflicts remaining: {len(conflicts)}
- List of conflicts: {', '.join(conflicts[:5])}

Answer the administrator's question in a professional, concise way (max 3 sentences). 
If they ask about resolving conflicts, suggest swapping students of conflicting subjects.
"""
    
    # Get Groq API key from environment
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key or "your_api_key_here" in groq_api_key:
        return jsonify({"response": "I am here to help you optimize the seating layout. Please add a valid GROQ_API_KEY to your .env file to enable live AI analysis!"})
        
    try:
        client = Groq(api_key=groq_api_key)
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            model="llama-3.1-8b-instant",
            max_tokens=256,
            temperature=0.7
        )
        ai_response = chat_completion.choices[0].message.content
        return jsonify({"response": ai_response})
    except Exception as e:
        return jsonify({"response": f"AI Copilot encountered an error contacting Groq: {str(e)}"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
