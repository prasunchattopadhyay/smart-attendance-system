"""
Smart Attendance System - Flask web app
Run:  python app.py      then open  http://127.0.0.1:5000
First login:  admin / admin123   (change it from the "Password" page)
"""
import os
import sqlite3
from functools import wraps
from urllib.parse import quote

from flask import (Flask, render_template, request, redirect, url_for, session,
                   Response, jsonify, send_from_directory, abort, flash)

import database as db
import reports

app = Flask(__name__)

# keep the same secret key between restarts so people stay logged in
_key_file = os.path.join(db.BASE_DIR, "secret.key")
if not os.path.exists(_key_file):
    with open(_key_file, "w") as f:
        f.write(os.urandom(32).hex())
with open(_key_file) as f:
    app.secret_key = f.read().strip()
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024   # registration sends ~12 photos

db.init_db()


def _rec():
    """Face library is loaded only when a camera page needs it."""
    import recognize
    return recognize


# ---------------- login / roles ----------------
def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "uid" not in session:
                if request.path.startswith("/api/"):
                    return jsonify(error="Please log in again."), 401
                return redirect(url_for("login", next=request.path))
            if roles and session.get("role") not in roles:
                if request.path.startswith("/api/"):
                    return jsonify(error="You do not have access to this."), 403
                flash("You do not have access to that page.", "error")
                return redirect(url_for("home"))
            return fn(*args, **kwargs)
        return wrapper
    return deco


STAFF = ("admin", "teacher")


@app.context_processor
def inject_user():
    return {"me": {"name": session.get("name"), "role": session.get("role")},
            "ep": request.endpoint or ""}


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = db.verify_login(request.form.get("username", ""), request.form.get("password", ""))
        if user:
            session.clear()
            session.update(uid=user["id"], role=user["role"],
                           name=user["full_name"] or user["username"], student_id=user["student_id"])
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//") and user["role"] != "student":
                return redirect(nxt)
            return redirect(url_for("home"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required()
def home():
    if session["role"] == "student":
        return redirect(url_for("my_attendance"))
    return redirect(url_for("dashboard"))


# ---------------- dashboard ----------------
@app.route("/dashboard")
@login_required(*STAFF)
def dashboard():
    conn = db.get_conn()
    n_sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE end_time IS NOT NULL").fetchone()[0]
    active = conn.execute("SELECT * FROM sessions WHERE end_time IS NULL ORDER BY id DESC LIMIT 1").fetchone()
    n_unknown = conn.execute("SELECT COUNT(*) FROM unknown_faces").fetchone()[0]
    recent = conn.execute("SELECT * FROM sessions WHERE end_time IS NOT NULL ORDER BY id DESC LIMIT 5").fetchall()
    conn.close()
    stats = reports.student_stats()
    at_risk = sorted([s for s in stats if s["at_risk"]], key=lambda s: s["percent"])
    overall = round(sum(s["percent"] for s in stats if s["total"]) /
                    max(1, len([s for s in stats if s["total"]])), 1)
    default_admin = (session["role"] == "admin" and db.verify_login("admin", "admin123") is not None)
    return render_template("dashboard.html", n_students=len(stats), n_sessions=n_sessions, active=active,
                           n_unknown=n_unknown, at_risk=at_risk[:6], n_at_risk=len(at_risk),
                           overall=overall, recent=recent, threshold=reports.threshold(),
                           default_admin=default_admin)


@app.route("/api/analytics")
@login_required(*STAFF)
def api_analytics():
    return jsonify(reports.analytics())


# ---------------- students ----------------
@app.route("/students")
@login_required(*STAFF)
def students():
    class_name = request.args.get("class_name") or None
    return render_template("students.html", stats=reports.student_stats(class_name),
                           classes=reports.class_names(), selected=class_name)


@app.route("/students/register")
@login_required(*STAFF)
def register_page():
    return render_template("register.html", classes=reports.class_names())


@app.route("/api/face_check", methods=["POST"])
@login_required(*STAFF)
def api_face_check():
    data = request.get_json(silent=True) or {}
    count, box = _rec().check_face(data.get("image", ""))
    return jsonify(faces=count, box=box)


@app.route("/api/register", methods=["POST"])
@login_required(*STAFF)
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    roll_no = (data.get("roll_no") or "").strip()
    class_name = (data.get("class_name") or "").strip()
    images = data.get("images") or []
    if not (name and roll_no and class_name):
        return jsonify(error="Name, roll number and class are all required."), 400
    if any(s["roll_no"].lower() == roll_no.lower() for s in db.get_students()):
        return jsonify(error=f"Roll number {roll_no} is already registered."), 409
    if len(images) < 8:
        return jsonify(error="Not enough photos were captured. Please try again."), 400

    rec = _rec()
    enc, msg = rec.encode_registration(images)
    if enc is None:
        return jsonify(error=msg), 422
    dup = rec.find_duplicate(enc)
    if dup:
        return jsonify(error=f"This face is already registered as {dup}."), 409
    try:
        db.add_student(name, roll_no, class_name, enc)
    except sqlite3.IntegrityError:
        return jsonify(error="That roll number is already registered."), 409
    rec.forget_known_faces()
    return jsonify(ok=True, message=f"{name} registered. Login: {roll_no} / {roll_no}")


@app.route("/students/<int:sid>/delete", methods=["POST"])
@login_required("admin")
def delete_student(sid):
    s = db.get_student(sid)
    if s:
        db.delete_student(sid)
        try:
            _rec().forget_known_faces()
        except Exception:
            pass
        flash(f"Deleted {s['name']} and their attendance records.", "ok")
    return redirect(url_for("students"))


@app.route("/student/<int:sid>")
@login_required(*STAFF)
def student_detail(sid):
    d = reports.student_detail(sid)
    if d is None:
        abort(404)
    return render_template("student_detail.html", d=d, own=False)


@app.route("/me")
@login_required("student")
def my_attendance():
    sid = session.get("student_id")
    d = reports.student_detail(sid) if sid else None
    if d is None:
        flash("Your student record was not found. Ask your teacher.", "error")
        return redirect(url_for("logout"))
    return render_template("student_detail.html", d=d, own=True)


# ---------------- sessions (live attendance) ----------------
@app.route("/session/start", methods=["GET", "POST"])
@login_required(*STAFF)
def session_start():
    classes = reports.class_names()
    if request.method == "POST":
        class_name = request.form.get("class_name", "").strip()
        subject = request.form.get("subject", "").strip()
        if not class_name or not subject:
            flash("Choose a class and enter a subject.", "error")
        elif not db.get_students(class_name):
            flash(f"No students are registered in class {class_name}.", "error")
        else:
            sid = db.start_session(class_name, subject, session.get("name"))
            return redirect(url_for("session_live", sid=sid))
    return render_template("session_start.html", classes=classes,
                           late=db.get_setting("late_minutes"),
                           blink=db.get_setting("require_blink") == "1")


def _session_or_404(sid):
    s = db.get_session(sid)
    if s is None:
        abort(404)
    return s


@app.route("/session/<int:sid>")
@login_required(*STAFF)
def session_live(sid):
    s = _session_or_404(sid)
    if s["end_time"]:
        return redirect(url_for("session_summary", sid=sid))
    return render_template("session_live.html", s=s,
                           blink=db.get_setting("require_blink") == "1")


@app.route("/api/recognize/<int:sid>", methods=["POST"])
@login_required(*STAFF)
def api_recognize(sid):
    s = _session_or_404(sid)
    if s["end_time"]:
        return jsonify(error="This session has ended."), 400
    data = request.get_json(silent=True) or {}
    return jsonify(_rec().process_frame(sid, s["class_name"], data.get("image", "")))


@app.route("/session/<int:sid>/status")
@login_required(*STAFF)
def session_status(sid):
    s = _session_or_404(sid)
    rows = reports.session_attendance(sid)
    pending = reports.pending_students(sid, s["class_name"])
    return jsonify(
        marked=[{"name": r["name"], "roll_no": r["roll_no"], "status": r["status"],
                 "time": r["time_marked"]} for r in rows],
        pending=[{"name": p["name"], "roll_no": p["roll_no"]} for p in pending],
        ended=bool(s["end_time"]))


@app.route("/session/<int:sid>/end", methods=["POST"])
@login_required(*STAFF)
def session_end(sid):
    s = _session_or_404(sid)
    if not s["end_time"]:
        db.end_session(sid)
        try:
            _rec().reset_session(sid)
        except Exception:
            pass
        flash("Session ended. Students who were not seen are marked Absent.", "ok")
    return redirect(url_for("session_summary", sid=sid))


@app.route("/session/<int:sid>/summary")
@login_required(*STAFF)
def session_summary(sid):
    s = _session_or_404(sid)
    rows = reports.session_attendance(sid)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("Present", "Late", "Absent")}
    return render_template("session_summary.html", s=s, rows=rows, counts=counts)


@app.route("/session/<int:sid>/set", methods=["POST"])
@login_required(*STAFF)
def session_set_status(sid):
    s = _session_or_404(sid)
    try:
        student = db.get_student(int(request.form["student_id"]))
        if student is None or student["class_name"] != s["class_name"]:
            raise ValueError("student is not in this session's class")
        db.set_attendance_status(student["id"], sid, request.form["status"])
        flash(f"{student['name']} is now marked {request.form['status']}.", "ok")
    except (ValueError, KeyError):
        flash("Could not update that record.", "error")
    return redirect(url_for("session_summary", sid=sid))


@app.route("/sessions")
@login_required(*STAFF)
def sessions_list():
    conn = db.get_conn()
    rows = conn.execute(
        """SELECT se.*, SUM(CASE WHEN a.status IN ('Present','Late') THEN 1 ELSE 0 END) AS att,
                  COUNT(a.id) AS n
           FROM sessions se LEFT JOIN attendance a ON a.session_id = se.id
           GROUP BY se.id ORDER BY se.id DESC""").fetchall()
    conn.close()
    return render_template("sessions.html", sessions=rows)


# ---------------- reports & alerts ----------------
@app.route("/reports")
@login_required(*STAFF)
def reports_page():
    class_name = request.args.get("class_name") or None
    return render_template("reports.html", stats=reports.student_stats(class_name),
                           classes=reports.class_names(), selected=class_name,
                           threshold=reports.threshold())


@app.route("/reports/export.csv")
@login_required(*STAFF)
def reports_csv():
    class_name = request.args.get("class_name") or None
    return Response(reports.export_csv(class_name), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=attendance_report.csv"})


@app.route("/alerts")
@login_required(*STAFF)
def alerts():
    items = []
    for s in reports.student_stats():
        if s["at_risk"]:
            msg = reports.alert_message(s)
            items.append({"s": s, "msg": msg,
                          "mailto": "mailto:?subject=" + quote("Low attendance warning") + "&body=" + quote(msg)})
    items.sort(key=lambda i: i["s"]["percent"])
    return render_template("alerts.html", items=items, threshold=reports.threshold())


# ---------------- unknown faces ----------------
@app.route("/unknown")
@login_required(*STAFF)
def unknown_page():
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM unknown_faces ORDER BY id DESC LIMIT 200").fetchall()
    conn.close()
    return render_template("unknown.html", rows=rows)


@app.route("/unknown_faces/<path:filename>")
@login_required(*STAFF)
def unknown_file(filename):
    return send_from_directory(db.UNKNOWN_DIR, os.path.basename(filename))


# ---------------- admin: users & settings ----------------
@app.route("/users", methods=["GET", "POST"])
@login_required("admin")
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "teacher")
        full_name = request.form.get("full_name", "").strip()
        if role not in ("teacher", "admin"):
            role = "teacher"
        if not username or len(password) < 6:
            flash("Enter a username and a password of at least 6 characters.", "error")
        else:
            try:
                db.create_user(username, password, role, full_name)
                flash(f"Account {username} created.", "ok")
            except sqlite3.IntegrityError:
                flash("That username is already taken.", "error")
        return redirect(url_for("users"))
    return render_template("users.html", users=db.list_users())


@app.route("/users/<int:uid>/reset", methods=["POST"])
@login_required("admin")
def user_reset(uid):
    pw = request.form.get("password", "")
    if len(pw) < 6:
        flash("New password must be at least 6 characters.", "error")
    elif db.get_user(uid):
        db.set_password(uid, pw)
        flash("Password changed.", "ok")
    return redirect(url_for("users"))


@app.route("/users/<int:uid>/delete", methods=["POST"])
@login_required("admin")
def user_delete(uid):
    u = db.get_user(uid)
    admins = [x for x in db.list_users() if x["role"] == "admin"]
    if u is None:
        pass
    elif u["id"] == session["uid"]:
        flash("You cannot delete your own account.", "error")
    elif u["role"] == "admin" and len(admins) <= 1:
        flash("At least one admin must remain.", "error")
    else:
        db.delete_user(uid)
        flash(f"Account {u['username']} deleted.", "ok")
    return redirect(url_for("users"))


@app.route("/settings", methods=["GET", "POST"])
@login_required("admin")
def settings():
    if request.method == "POST":
        try:
            late = int(request.form["late_minutes"])
            thr = int(request.form["threshold"])
            tol = float(request.form["tolerance"])
            assert 1 <= late <= 180 and 50 <= thr <= 95 and 0.35 <= tol <= 0.65
        except (ValueError, KeyError, AssertionError):
            flash("Check the values: late 1-180 minutes, minimum 50-95%, strictness 0.35-0.65.", "error")
            return redirect(url_for("settings"))
        db.set_setting("late_minutes", late)
        db.set_setting("threshold", thr)
        db.set_setting("tolerance", tol)
        db.set_setting("require_blink", "1" if request.form.get("require_blink") else "0")
        flash("Settings saved.", "ok")
        return redirect(url_for("settings"))
    return render_template("settings.html", s={k: db.get_setting(k) for k in db.DEFAULT_SETTINGS})


@app.route("/password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        user = db.get_user(session["uid"])
        cur, new, again = (request.form.get(k, "") for k in ("current", "new", "again"))
        if not db.verify_login(user["username"], cur):
            flash("Your current password is wrong.", "error")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new != again:
            flash("The two new passwords do not match.", "error")
        else:
            db.set_password(user["id"], new)
            flash("Password changed.", "ok")
            return redirect(url_for("home"))
    return render_template("password.html")


@app.errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404


if __name__ == "__main__":
    print("\n  Smart Attendance is running.  Open  http://127.0.0.1:5000  in Chrome or Edge")
    print("  First login:  admin / admin123\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
