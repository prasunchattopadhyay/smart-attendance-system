"""
Step 5 - Face Registration
Run:   python register_face.py
       python register_face.py --list      (show registered students)

Captures several frames of ONE face from the webcam, converts each to a
128-number encoding, averages them and stores the result in the database.
No photo is stored (privacy by design).
"""
import sys
import time
import warnings

warnings.filterwarnings("ignore", category=UserWarning)

import cv2
import numpy as np
import face_recognition

import database as db

SAMPLES_NEEDED = 12        # how many good frames to capture
CAPTURE_GAP = 0.6          # seconds between captures
MAX_SPREAD = 0.45          # samples farther than this from the first are rejected
DUPLICATE_DISTANCE = 0.45  # new face closer than this to an existing student = duplicate


def list_students():
    rows = db.get_students()
    if not rows:
        print("No students registered yet.")
        return
    print(f"{'ID':<4}{'Roll No':<12}{'Name':<25}{'Class'}")
    print("-" * 55)
    for r in rows:
        print(f"{r['id']:<4}{r['roll_no']:<12}{r['name']:<25}{r['class_name']}")


def capture_encodings(name):
    """Opens the webcam and returns a list of encodings, or None if cancelled."""
    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        print("ERROR: cannot open webcam.")
        return None

    encodings = []
    last_capture = 0.0
    message = "Look at the camera"

    print("\nWebcam window opened. Look straight at the camera.")
    print("Slowly turn your head a little left/right/up/down while it captures.")
    print("Press Q to cancel.\n")

    while len(encodings) < SAMPLES_NEEDED:
        ok, frame = cam.read()
        if not ok:
            print("ERROR: could not read from webcam.")
            break

        # detect on a smaller frame for speed
        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb_small)

        color = (0, 0, 255)
        if len(locations) == 0:
            message = "No face found - move closer / improve light"
        elif len(locations) > 1:
            message = "More than one face - only ONE person please"
        else:
            color = (0, 200, 0)
            message = "Face OK - capturing..."
            top, right, bottom, left = [v * 2 for v in locations[0]]
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

            now = time.time()
            if now - last_capture >= CAPTURE_GAP:
                rgb_full = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                enc = face_recognition.face_encodings(
                    rgb_full, known_face_locations=[(top, right, bottom, left)]
                )
                if enc:
                    if encodings:
                        dist = face_recognition.face_distance([encodings[0]], enc[0])[0]
                        if dist > MAX_SPREAD:
                            message = "Hold still, look at the camera"
                            last_capture = now
                            cv2.putText(frame, message, (10, 30),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
                            cv2.imshow("Register Face", frame)
                            if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
                                cam.release()
                                cv2.destroyAllWindows()
                                return None
                            continue
                    encodings.append(enc[0])
                    last_capture = now

        cv2.putText(frame, f"{name}: {len(encodings)}/{SAMPLES_NEEDED}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, message, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        cv2.imshow("Register Face", frame)

        if cv2.waitKey(1) & 0xFF in (ord("q"), ord("Q")):
            print("Cancelled.")
            cam.release()
            cv2.destroyAllWindows()
            return None

    cam.release()
    cv2.destroyAllWindows()
    return encodings if len(encodings) == SAMPLES_NEEDED else None


def main():
    db.init_db()

    if "--list" in sys.argv:
        list_students()
        return

    print("=== Register New Student ===")
    name = input("Full name   : ").strip()
    roll_no = input("Roll number : ").strip()
    class_name = input("Class       : ").strip()

    if not (name and roll_no and class_name):
        print("All fields are required.")
        return

    # roll number must be unique
    for s in db.get_students():
        if s["roll_no"].lower() == roll_no.lower():
            print(f"Roll number {roll_no} is already registered ({s['name']}).")
            return

    encodings = capture_encodings(name)
    if not encodings:
        print("Registration not completed.")
        return

    final = np.mean(encodings, axis=0)   # average = more stable than one photo

    # block the same face being registered twice under different names
    ids, names, known = db.load_known_faces()
    if known:
        dists = face_recognition.face_distance(known, final)
        best = int(np.argmin(dists))
        if dists[best] < DUPLICATE_DISTANCE:
            print(f"This face already looks registered as '{names[best]}'. Not saved.")
            return

    db.add_student(name, roll_no, class_name, final)
    print(f"\nSaved: {name} (Roll {roll_no}, Class {class_name})")
    print("Run  python register_face.py --list  to see all students.")


if __name__ == "__main__":
    main()
