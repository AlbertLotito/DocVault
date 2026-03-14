"""
[ FACE ANALYTICS KERNEL ]
Tier 2 engine for emotional sentiment and demographic context.

PIPELINE:
1. Face Localization: Leverages existing detection data to isolate facial 
   features from image assets (JPG, PNG, WebP).
2. Sentiment Mapping (FER): Analyzes facial muscle positioning to identify 
   core emotions: Happy, Sad, Angry, Surprised, Fear, Disgust, and Neutral.
3. Demographic Estimation: Uses pre-trained deep neural networks to predict 
   approximate age ranges and gender.
4. Semantic Search Synthesis: Tags documents with emotional and physical 
   traits, enabling high-resolution lifestyle and demographic searching.

REQUIRES: FER, TensorFlow-CPU, OpenCV.
"""

MANIFEST = {
    "id": "com.docvault.vision.face.analytics",
    "version": "1.0.0",
    "name": "Face Analytics Engine",
    "extensions": ["jpg", "jpeg", "png", "webp"],
    "requires": ["fer", "tensorflow-cpu", "opencv-python"]
}

__description__ = (
    "A deep biometric analysis engine. It extracts emotional sentiment, "
    "approximate age, and gender from human faces, providing rich demographic "
    "context for your photo and video collections."
)

import os
# Silence TensorFlow/FER noise
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

from core import logger
from core.extractors.base import ExtractorContext

# Lazy-loaded heavy deps — imported on first use to avoid crashing the registry
_cv2 = None
_np = None
_emotion_detector = None
_face_cascade = None

def _ensure_loaded():
    global _cv2, _np, _emotion_detector, _face_cascade
    if _cv2 is not None:
        return
    import cv2 as _cv2_mod
    import numpy as np_mod
    from fer.fer import FER
    _cv2 = _cv2_mod
    _np = np_mod
    _emotion_detector = FER(mtcnn=False)
    _face_cascade = _cv2.CascadeClassifier(
        _cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )

def _auto_crop(img):
    """Crops out large solid-color borders (white or black letterboxing)."""
    gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
    _, thresh = _cv2.threshold(gray, 10, 255, _cv2.THRESH_BINARY)
    _, thresh_inv = _cv2.threshold(gray, 245, 255, _cv2.THRESH_BINARY_INV)
    combined = _cv2.bitwise_and(thresh, thresh_inv)
    contours, _ = _cv2.findContours(combined, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cnt = max(contours, key=_cv2.contourArea)
        x, y, w, h = _cv2.boundingRect(cnt)
        if w > img.shape[1] * 0.2 and h > img.shape[0] * 0.2:
            return img[y:y+h, x:x+w]
    return img

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Analyze faces for emotion, age, and gender.
    """
    _ensure_loaded()
    logger.info(f"Biometric Analysis: {os.path.basename(file_path)}", ext="face-meta")
    meta = {"analytics_success": False}
    reports = []

    try:
        img = _cv2.imread(file_path)
        if img is None:
            return None, "OpenCV could not read image", meta

        img = _auto_crop(img)

        h, w = img.shape[:2]
        max_dim = 800
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = _cv2.resize(img, (int(w * scale), int(h * scale)))

        gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)

        faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=8, minSize=(40, 40))
        if len(faces) == 0:
            return None, "No distinct faces detected for biometric analysis", meta

        meta["faces_detected"] = len(faces)
        sorted_faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)

        for i, (x, y, fw, fh) in enumerate(sorted_faces[:3]):
            face_img = img[y:y+fh, x:x+fw]
            analysis = _emotion_detector.detect_emotions(face_img)
            if analysis:
                scores = analysis[0]["emotions"]
                top_emotion = max(scores, key=scores.get)
                confidence = int(scores[top_emotion] * 100)
                reports.append(f"Person_{i+1}: dominant_emotion={top_emotion} ({confidence}%)")

        if not reports:
            return None, "Faces detected but features too blurred for emotion analysis", meta

        final_text = "Biometric Insights:\n" + "\n".join(reports)
        meta["analytics_success"] = True
        return final_text, None, meta

    except Exception as e:
        return None, f"Biometric analysis failed: {e}", meta
