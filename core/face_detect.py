"""Cheap face detection shared by the face kernels (OpenCV Haar cascade).

Same parameters as face_identity_extractor, so "has faces" means the same thing
everywhere. Used to gate the vision-model narrative, which otherwise invents
people for images that contain none.
"""
_cv2 = None
_cascade = None


def _load():
    global _cv2, _cascade
    if _cascade is None:
        import cv2
        _cv2 = cv2
        _cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    return _cv2, _cascade


def detect_faces(file_path: str) -> list:
    """(x, y, w, h) boxes of frontal faces, largest first; [] if none or unreadable."""
    cv2, cascade = _load()
    img = cv2.imread(file_path)
    if img is None:
        return []
    h, w = img.shape[:2]
    if max(h, w) > 1200:
        scale = 1200 / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(40, 40))
    return sorted((tuple(f) for f in faces), key=lambda f: f[2] * f[3], reverse=True)


def count_faces(file_path: str) -> int:
    try:
        return len(detect_faces(file_path))
    except Exception:
        return 0
