"""SQLite database: students, sessions, attendance, users, settings."""
import os
import sqlite3
from datetime import datetime

import numpy as np
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "attendance.db")
UNKNOWN_DIR = os.path.join(BASE_DIR, "unknown_faces")

DEFAULT_SETTINGS = {
    "late_minutes": "10",      # scans after this many minutes count as Late
    "threshold": "75",         # minimum attendance percentage
    "require_blink": "1",      # liveness check on/off
    "tolerance": "0.5",        # face match strictness (lower = stricter)
}


def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        roll_no TEXT UNIQUE NOT NULL,
        class_name TEXT NOT NULL,
        encoding BLOB NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_name TEXT NOT NULL,
        subject TEXT NOT NULL,
        date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT,
        created_by TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        time_marked TEXT NOT NULL,
        status TEXT NOT NULL,
        UNIQUE(student_id, session_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS unknown_faces (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        image_path TEXT,
        time_seen TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        full_name TEXT NOT NULL DEFAULT '',
        student_id INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    # older databases (from Step 4) have no created_by column
    cols = [r["name"] for r in c.execute("PRAGMA table_info(sessions)").fetchall()]
    if "created_by" not in cols:
        c.execute("ALTER TABLE sessions ADD COLUMN created_by TEXT")
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.commit()

    # first run: create the admin account
    if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        c.execute("INSERT INTO users (username, password_hash, role, full_name) VALUES (?,?,?,?)",
                  ("admin", generate_password_hash("admin123"), "admin", "Administrator"))
    # students registered before accounts existed get a login (username = roll no)
    for s in c.execute("""SELECT id, name, roll_no FROM students
                          WHERE id NOT IN (SELECT student_id FROM users WHERE student_id IS NOT NULL)""").fetchall():
        c.execute("INSERT OR IGNORE INTO users (username, password_hash, role, full_name, student_id) VALUES (?,?,?,?,?)",
                  (s["roll_no"], generate_password_hash(s["roll_no"]), "student", s["name"], s["id"]))
    conn.commit()
    conn.close()
    os.makedirs(UNKNOWN_DIR, exist_ok=True)


# ---------- Settings ----------
def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if row:
        return row["value"]
    return DEFAULT_SETTINGS.get(key, default)


def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


# ---------- Users ----------
def verify_login(username, password):
    conn = get_conn()
    u = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    conn.close()
    if u and check_password_hash(u["password_hash"], password):
        return u
    return None


def get_user(uid):
    conn = get_conn()
    u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    conn.close()
    return u


def list_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY role, username").fetchall()
    conn.close()
    return rows


def create_user(username, password, role, full_name="", student_id=None):
    """Raises sqlite3.IntegrityError if the username already exists."""
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, full_name, student_id) VALUES (?,?,?,?,?)",
            (username.strip(), generate_password_hash(password), role, full_name, student_id))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def set_password(uid, password):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), uid))
    conn.commit()
    conn.close()


def delete_user(uid):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()


# ---------- Students ----------
def add_student(name, roll_no, class_name, encoding, create_login=True):
    """encoding = numpy array of 128 floats (average of several captures). Returns new id."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO students (name, roll_no, class_name, encoding) VALUES (?,?,?,?)",
        (name, roll_no, class_name, encoding.astype(np.float64).tobytes()))
    sid = cur.lastrowid
    if create_login:
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, role, full_name, student_id) VALUES (?,?,?,?,?)",
            (roll_no, generate_password_hash(roll_no), "student", name, sid))
    conn.commit()
    conn.close()
    return sid


def get_student(sid):
    conn = get_conn()
    s = conn.execute("SELECT id, name, roll_no, class_name FROM students WHERE id=?", (sid,)).fetchone()
    conn.close()
    return s


def get_students(class_name=None):
    conn = get_conn()
    if class_name:
        rows = conn.execute("SELECT * FROM students WHERE class_name=? ORDER BY roll_no", (class_name,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM students ORDER BY class_name, roll_no").fetchall()
    conn.close()
    return rows


def load_known_faces(class_name=None):
    """Returns (ids, names, encodings) ready for face_recognition"""
    ids, names, encs = [], [], []
    for r in get_students(class_name):
        ids.append(r["id"])
        names.append(r["name"])
        encs.append(np.frombuffer(r["encoding"], dtype=np.float64))
    return ids, names, encs


def delete_student(sid):
    conn = get_conn()
    conn.execute("DELETE FROM attendance WHERE student_id=?", (sid,))
    conn.execute("DELETE FROM users WHERE student_id=?", (sid,))
    conn.execute("DELETE FROM students WHERE id=?", (sid,))
    conn.commit()
    conn.close()


# ---------- Sessions ----------
def start_session(class_name, subject, created_by=None):
    now = datetime.now()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO sessions (class_name, subject, date, start_time, created_by) VALUES (?,?,?,?,?)",
        (class_name, subject, now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), created_by))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def get_session(sid):
    conn = get_conn()
    s = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    conn.close()
    return s


def end_session(session_id):
    """Closes the session and marks everyone unmarked as Absent"""
    now = datetime.now().strftime("%H:%M:%S")
    conn = get_conn()
    conn.execute("UPDATE sessions SET end_time=? WHERE id=?", (now, session_id))
    sess = conn.execute("SELECT class_name FROM sessions WHERE id=?", (session_id,)).fetchone()
    students = conn.execute("SELECT id FROM students WHERE class_name=?", (sess["class_name"],)).fetchall()
    for s in students:
        conn.execute(
            "INSERT OR IGNORE INTO attendance (student_id, session_id, time_marked, status) VALUES (?,?,?,?)",
            (s["id"], session_id, now, "Absent"))
    conn.commit()
    conn.close()


# ---------- Attendance ----------
def mark_attendance(student_id, session_id, late_after_minutes=None):
    """Marks Present or Late. Returns status, or None if already marked."""
    if late_after_minutes is None:
        late_after_minutes = float(get_setting("late_minutes", "10"))
    conn = get_conn()
    sess = conn.execute("SELECT date, start_time FROM sessions WHERE id=?", (session_id,)).fetchone()
    start = datetime.strptime(f"{sess['date']} {sess['start_time']}", "%Y-%m-%d %H:%M:%S")
    minutes = (datetime.now() - start).total_seconds() / 60
    status = "Present" if minutes <= late_after_minutes else "Late"
    try:
        conn.execute(
            "INSERT INTO attendance (student_id, session_id, time_marked, status) VALUES (?,?,?,?)",
            (student_id, session_id, datetime.now().strftime("%H:%M:%S"), status))
        conn.commit()
        return status
    except sqlite3.IntegrityError:
        return None  # already marked (duplicate prevention)
    finally:
        conn.close()


def set_attendance_status(student_id, session_id, status):
    """Teacher correction: set Present / Late / Absent for one student in one session."""
    if status not in ("Present", "Late", "Absent"):
        raise ValueError("bad status")
    conn = get_conn()
    conn.execute(
        "INSERT INTO attendance (student_id, session_id, time_marked, status) VALUES (?,?,?,?) "
        "ON CONFLICT(student_id, session_id) DO UPDATE SET status=excluded.status",
        (student_id, session_id, datetime.now().strftime("%H:%M:%S"), status))
    conn.commit()
    conn.close()


def log_unknown(session_id, image_path):
    conn = get_conn()
    conn.execute(
        "INSERT INTO unknown_faces (session_id, image_path, time_seen) VALUES (?,?,?)",
        (session_id, image_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Database ready:", DB)
