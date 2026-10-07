import cv2
import face_recognition
import numpy as np

print("OpenCV:", cv2.__version__)
print("face_recognition imported OK")

cam = cv2.VideoCapture(0)
ok, frame = cam.read()
cam.release()
print("Webcam working:", ok)

if ok:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    faces = face_recognition.face_locations(rgb)
    print("Faces found in test frame:", len(faces))
