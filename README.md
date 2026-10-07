# Smart Attendance System

Face recognition attendance with blink (liveness) check, roles, analytics and low-attendance alerts.

## Install (Windows PowerShell, inside this folder)
```
python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install "setuptools<81"
venv\Scripts\python.exe -m pip install numpy opencv-python pandas flask matplotlib pillow click
venv\Scripts\python.exe -m pip install dlib-bin face_recognition_models
venv\Scripts\python.exe -m pip install face_recognition --no-deps
```

## Run
```
venv\Scripts\python.exe app.py
```
Open http://127.0.0.1:5000 in Chrome or Edge (use this exact address, the camera needs it).

## Logins
- Admin: `admin` / `admin123` (change it on the Password page)
- Teachers: created by the admin on the Accounts page
- Students: username = roll number, password = roll number (created automatically)

## Files
- app.py            Flask app: login, roles, all pages and APIs
- database.py       SQLite tables and functions
- recognize.py      face detection, recognition, blink liveness (frames come from the browser)
- reports.py        percentages, predictor (x = 3T - 4A), analytics, CSV export
- register_face.py  optional command-line registration (the website can do it too)
- test_setup.py     checks OpenCV, face_recognition and the webcam
- templates/ static/   web pages, stylesheet, scripts, Chart.js (works offline)
- attendance.db     created on first run (delete it to start fresh)
