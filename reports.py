"""Attendance percentages, at-risk predictor, analytics and CSV export."""
import csv
import io
import math

import database as db

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def threshold():
    try:
        return min(99, max(1, int(float(db.get_setting("threshold", "75")))))
    except ValueError:
        return 75


def classes_needed(attended, total, thr=None):
    """Consecutive classes a student must attend to reach the threshold.
    For 75%:  x = 3T - 4A   (general: x = (t*T - 100*A) / (100 - t))"""
    thr = thr or threshold()
    if total == 0:
        return 0
    x = (thr * total - 100 * attended) / (100 - thr)
    return max(0, math.ceil(x - 1e-9))


def _row_to_stat(r, thr):
    present, late, absent = r["p"], r["l"], r["ab"]
    total = present + late + absent
    attended = present + late
    pct = round(100 * attended / total, 1) if total else 0.0
    return {
        "id": r["id"], "name": r["name"], "roll_no": r["roll_no"], "class_name": r["class_name"],
        "total": total, "present": present, "late": late, "absent": absent,
        "percent": pct, "at_risk": total > 0 and pct < thr,
        "needed": classes_needed(attended, total, thr),
    }


def student_stats(class_name=None):
    thr = threshold()
    sql = """SELECT st.id, st.name, st.roll_no, st.class_name,
               COALESCE(SUM(CASE WHEN x.status='Present' THEN 1 END), 0) AS p,
               COALESCE(SUM(CASE WHEN x.status='Late' THEN 1 END), 0) AS l,
               COALESCE(SUM(CASE WHEN x.status='Absent' THEN 1 END), 0) AS ab
             FROM students st
             LEFT JOIN (SELECT a.student_id, a.status FROM attendance a
                        JOIN sessions se ON se.id = a.session_id
                        WHERE se.end_time IS NOT NULL) x ON x.student_id = st.id
             {where} GROUP BY st.id ORDER BY st.class_name, st.roll_no"""
    conn = db.get_conn()
    if class_name:
        rows = conn.execute(sql.format(where="WHERE st.class_name=?"), (class_name,)).fetchall()
    else:
        rows = conn.execute(sql.format(where="")).fetchall()
    conn.close()
    return [_row_to_stat(r, thr) for r in rows]


def student_detail(student_id):
    """Stats + subject-wise breakdown + recent history for one student."""
    stat = next((s for s in student_stats() if s["id"] == student_id), None)
    if stat is None:
        return None
    conn = db.get_conn()
    subjects = conn.execute(
        """SELECT se.subject,
             SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) AS p,
             SUM(CASE WHEN a.status='Late' THEN 1 ELSE 0 END) AS l,
             SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) AS ab
           FROM attendance a JOIN sessions se ON se.id = a.session_id
           WHERE a.student_id=? AND se.end_time IS NOT NULL
           GROUP BY se.subject ORDER BY se.subject""", (student_id,)).fetchall()
    history = conn.execute(
        """SELECT se.date, se.subject, a.status, a.time_marked
           FROM attendance a JOIN sessions se ON se.id = a.session_id
           WHERE a.student_id=? AND se.end_time IS NOT NULL
           ORDER BY se.id DESC LIMIT 30""", (student_id,)).fetchall()
    conn.close()
    thr = threshold()
    subj = []
    for r in subjects:
        total = r["p"] + r["l"] + r["ab"]
        attended = r["p"] + r["l"]
        pct = round(100 * attended / total, 1) if total else 0.0
        subj.append({"subject": r["subject"], "total": total, "attended": attended,
                     "percent": pct, "at_risk": pct < thr})
    return {"stat": stat, "subjects": subj, "history": history, "threshold": thr}


def class_names():
    conn = db.get_conn()
    rows = conn.execute("SELECT DISTINCT class_name FROM students ORDER BY class_name").fetchall()
    conn.close()
    return [r["class_name"] for r in rows]


def session_attendance(session_id):
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT s.id AS student_id, s.name, s.roll_no, a.status, a.time_marked
           FROM attendance a JOIN students s ON s.id = a.student_id
           WHERE a.session_id=? ORDER BY a.time_marked, s.roll_no""", (session_id,)).fetchall()
    conn.close()
    return rows


def pending_students(session_id, class_name):
    """Students of the class not yet marked in this session."""
    marked = {r["student_id"] for r in session_attendance(session_id)}
    return [s for s in db.get_students(class_name) if s["id"] not in marked]


def analytics():
    """Data for the dashboard charts."""
    conn = db.get_conn()
    trend_rows = conn.execute(
        """SELECT se.id, se.date, se.subject, se.class_name, COUNT(a.id) AS n,
             SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS att
           FROM sessions se LEFT JOIN attendance a ON a.session_id = se.id
           WHERE se.end_time IS NOT NULL GROUP BY se.id ORDER BY se.id DESC LIMIT 15""").fetchall()
    trend = [{"label": f"{r['date'][5:]} {r['subject']}",
              "rate": round(100 * (r["att"] or 0) / r["n"], 1) if r["n"] else 0}
             for r in reversed(trend_rows)]

    wd_rows = conn.execute(
        """SELECT strftime('%w', se.date) AS wd, COUNT(*) AS n,
             SUM(CASE WHEN a.status='Absent' THEN 1 ELSE 0 END) AS ab
           FROM attendance a JOIN sessions se ON se.id = a.session_id
           WHERE se.end_time IS NOT NULL GROUP BY wd ORDER BY wd""").fetchall()
    weekday = [{"day": WEEKDAYS[int(r["wd"])], "absent_rate": round(100 * r["ab"] / r["n"], 1)} for r in wd_rows]

    st_rows = conn.execute(
        """SELECT a.status, COUNT(*) AS n FROM attendance a JOIN sessions se ON se.id = a.session_id
           WHERE se.end_time IS NOT NULL GROUP BY a.status""").fetchall()
    conn.close()
    status = {"Present": 0, "Late": 0, "Absent": 0}
    for r in st_rows:
        status[r["status"]] = r["n"]

    by_class = {}
    for s in student_stats():
        by_class.setdefault(s["class_name"], []).append(s["percent"])
    class_avg = [{"class_name": c, "avg": round(sum(v) / len(v), 1)} for c, v in sorted(by_class.items())]
    return {"trend": trend, "weekday": weekday, "status": status,
            "class_avg": class_avg, "threshold": threshold()}


def alert_message(s):
    """Plain-text warning a teacher can copy or email to an at-risk student."""
    thr = threshold()
    return (f"Dear {s['name']},\n\n"
            f"Your attendance in {s['class_name']} is {s['percent']}%, which is below the required {thr}%. "
            f"You have attended {s['present'] + s['late']} of {s['total']} classes.\n"
            f"To reach {thr}% you must attend the next {s['needed']} classes in a row without missing any.\n\n"
            f"Please contact your teacher if there is a genuine reason for your absences.\n\n"
            f"Regards,\nSmart Attendance System")


def export_csv(class_name=None):
    out = io.StringIO()
    w = csv.writer(out)
    thr = threshold()
    w.writerow(["Roll No", "Name", "Class", "Sessions", "Present", "Late", "Absent",
                "Percent", f"Classes needed for {thr}%"])
    for r in student_stats(class_name):
        w.writerow([r["roll_no"], r["name"], r["class_name"], r["total"], r["present"],
                    r["late"], r["absent"], r["percent"], r["needed"]])
    return out.getvalue()
