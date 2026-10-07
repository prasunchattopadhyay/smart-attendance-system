"""Face detection, recognition and blink liveness for frames sent by the browser.

The browser captures webcam frames and posts them here as JPEG data-URLs.
Nothing is stored except 128-number face encodings (and snapshots of unknown faces).
"""
import base64
import os
import threading
import time
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", category=UserWarning)

import cv2
import numpy as np
import face_recognition

import database as db

WORK_WIDTH = 480          # frames are shrunk to this width before processing (speed)
UNKNOWN_GAP = 10          # seconds between unknown-face snapshots per session
BLINK_CLOSED = 0.72       # eye is "closed" when EAR drops below 72% of this person's open EAR
BLINK_REOPEN = 0.85       # ...and "open again" when it climbs back above 85%

_lock = threading.Lock()
_sessions = {}            # session_id -> {"marked": set, "blink": {student_id: tracker}, "last_unknown": float}
_known_cache = {}         # class_name -> (loaded_at, ids, names, encodings)


# ---------------- helpers ----------------
def decode_image(data_url):
    """data-URL or raw base64 -> BGR image (or None)."""
    try:
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        arr = np.frombuffer(base64.b64decode(b64), np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _prepare(img, width=WORK_WIDTH):
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)))
    return img, cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def _known(class_name):
    now = time.time()
    cached = _known_cache.get(class_name)
    if cached and now - cached[0] < 15:
        return cached[1:]
    ids, names, encs = db.load_known_faces(class_name)
    _known_cache[class_name] = (now, ids, names, encs)
    return ids, names, encs


def forget_known_faces():
    _known_cache.clear()


def _ear(eye):
    """Eye aspect ratio from 6 landmark points (small value = eye closed)."""
    p = np.array(eye, dtype=float)
    a = np.linalg.norm(p[1] - p[5])
    b = np.linalg.norm(p[2] - p[4])
    c = np.linalg.norm(p[0] - p[3])
    return (a + b) / (2.0 * c) if c else 0.0


def _blinked(tracker, ear):
    """Feeds one EAR reading; returns True once open -> closed -> open has been seen."""
    tracker["n"] += 1
    tracker["max"] = max(ear, tracker["max"] * 0.995)
    if tracker["n"] < 2:
        return tracker["verified"]
    if ear < BLINK_CLOSED * tracker["max"]:
        tracker["closed"] = True
    elif tracker["closed"] and ear > BLINK_REOPEN * tracker["max"]:
        tracker["verified"] = True
    return tracker["verified"]


def _session_state(session_id):
    st = _sessions.get(session_id)
    if st is None:
        conn = db.get_conn()
        rows = conn.execute("SELECT student_id FROM attendance WHERE session_id=?", (session_id,)).fetchall()
        conn.close()
        st = {"marked": {r["student_id"] for r in rows}, "blink": {}, "last_unknown": 0.0}
        _sessions[session_id] = st
    return st


def reset_session(session_id):
    with _lock:
        _sessions.pop(session_id, None)


# ---------------- live attendance ----------------
def process_frame(session_id, class_name, data_url):
    """Recognises faces in one frame and marks attendance. Returns a dict for the browser."""
    img = decode_image(data_url)
    if img is None:
        return {"faces": [], "error": "bad image"}
    small, rgb = _prepare(img)
    h, w = small.shape[:2]

    require_blink = db.get_setting("require_blink", "1") == "1"
    tolerance = float(db.get_setting("tolerance", "0.5"))

    locs = face_recognition.face_locations(rgb)
    encs = face_recognition.face_encodings(rgb, locs)
    marks = face_recognition.face_landmarks(rgb, locs) if (require_blink and locs) else [None] * len(locs)

    ids, names, known = _known(class_name)
    faces = []
    with _lock:
        st = _session_state(session_id)
        for (t, r, b, l), enc, lm in zip(locs, encs, marks):
            box = [l / w, t / h, r / w, b / h]
            idx = None
            if known:
                d = face_recognition.face_distance(known, enc)
                best = int(np.argmin(d))
                if d[best] <= tolerance:
                    idx = best

            if idx is None:
                faces.append({"box": box, "label": "Unknown", "state": "unknown", "text": "Not registered"})
                if time.time() - st["last_unknown"] > UNKNOWN_GAP:
                    st["last_unknown"] = time.time()
                    _save_unknown(session_id, img, small.shape, (t, r, b, l))
                continue

            sid, name = ids[idx], names[idx]
            if sid in st["marked"]:
                faces.append({"box": box, "label": name, "state": "already", "text": "Already marked"})
                continue

            if require_blink and lm:
                tr = st["blink"].setdefault(sid, {"n": 0, "max": 0.0, "closed": False, "verified": False})
                ear = (_ear(lm["left_eye"]) + _ear(lm["right_eye"])) / 2
                if not _blinked(tr, ear):
                    faces.append({"box": box, "label": name, "state": "blink", "text": "Blink once to confirm"})
                    continue

            status = db.mark_attendance(sid, session_id)
            st["marked"].add(sid)
            if status:
                faces.append({"box": box, "label": name, "state": "marked", "text": f"Marked {status}"})
            else:
                faces.append({"box": box, "label": name, "state": "already", "text": "Already marked"})
    return {"faces": faces}


def _save_unknown(session_id, img, small_shape, loc):
    """Crops the unknown face from the full-size frame and logs it."""
    try:
        sh, sw = small_shape[:2]
        H, W = img.shape[:2]
        t, r, b, l = loc
        fx, fy = W / sw, H / sh
        pad = 20
        crop = img[max(int(t * fy) - pad, 0):int(b * fy) + pad, max(int(l * fx) - pad, 0):int(r * fx) + pad]
        if crop.size == 0:
            return
        os.makedirs(db.UNKNOWN_DIR, exist_ok=True)
        fname = f"unknown_{session_id}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        cv2.imwrite(os.path.join(db.UNKNOWN_DIR, fname), crop)
        db.log_unknown(session_id, fname)
    except Exception:
        pass


# ---------------- registration ----------------
def check_face(data_url):
    """Quick check used while registering. Returns (face_count, box or None)."""
    img = decode_image(data_url)
    if img is None:
        return 0, None
    small, rgb = _prepare(img)
    h, w = small.shape[:2]
    locs = face_recognition.face_locations(rgb)
    if len(locs) != 1:
        return len(locs), None
    t, r, b, l = locs[0]
    return 1, [l / w, t / h, r / w, b / h]


def encode_registration(data_urls, min_samples=8, max_spread=0.5):
    """Turns many frames of ONE person into a single averaged encoding.
    Returns (encoding or None, message)."""
    encodings = []
    for u in data_urls:
        img = decode_image(u)
        if img is None:
            continue
        _, rgb = _prepare(img, width=640)
        locs = face_recognition.face_locations(rgb)
        if len(locs) != 1:
            continue
        enc = face_recognition.face_encodings(rgb, locs)
        if enc:
            encodings.append(enc[0])
    if len(encodings) < min_samples:
        return None, (f"Only {len(encodings)} usable photos out of {len(data_urls)}. "
                      "Face the camera with light on your face and keep only one person in view.")
    ref = np.median(np.array(encodings), axis=0)
    dist = face_recognition.face_distance(encodings, ref)
    good = [e for e, d in zip(encodings, dist) if d <= max_spread]
    if len(good) < min_samples:
        return None, "The photos did not look like the same person. Try again and keep still."
    return np.mean(good, axis=0), f"{len(good)} photos used"


def find_duplicate(encoding, distance=0.45):
    """If this face already belongs to a registered student, returns that student's name."""
    ids, names, known = db.load_known_faces()
    if not known:
        return None
    d = face_recognition.face_distance(known, encoding)
    best = int(np.argmin(d))
    return names[best] if d[best] < distance else None
