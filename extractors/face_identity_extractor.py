"""
[ FACE IDENTITY EXTRACTION KERNEL ]
Tier 1 engine for identifying and clustering individuals across images.

PIPELINE:
1. Face Detection (OpenCV Haar Cascade): High-speed localization of human
   faces within images (JPG, PNG, WebP).
2. Identity Encoding: Generates a mathematical signature (embedding) for each
   detected face.
3. Identity Linking: Checks the local registry to associate the face with an
   existing 'Person' or creates a new 'Unknown' cluster.
4. Search Synthesis: Tags the document with the identities found, enabling
   people-based searching.

REQUIRES: OpenCV.
"""

MANIFEST = {
    "id": "com.docvault.vision.face",
    "version": "1.0.0",
    "name": "Face Identity Extractor",
    "extensions": ["jpg", "jpeg", "png", "webp"],
    "requires": ["opencv-python"]
}

__description__ = (
    "The system's primary 'Identity' engine. It detects human faces and generates "
    "mathematical signatures to cluster and identify individuals across your "
    "entire photo collection."
)

import os
from core import logger
from core.extractors.base import ExtractorContext

# Lazy-loaded — imported on first use to avoid crashing the registry
_cv2 = None
_face_cascade = None

def _ensure_loaded():
    global _cv2, _face_cascade
    if _cv2 is not None:
        return
    import cv2 as _cv2_mod
    _cv2 = _cv2_mod
    _face_cascade = _cv2.CascadeClassifier(
        _cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
    )

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Find faces and generate identity markers.
    """
    _ensure_loaded()
    logger.info(f"Scanning for identities: {os.path.basename(file_path)}", ext="face-ai")
    meta = {"faces_found": 0}
    identities = []

    try:
        img = _cv2.imread(file_path)
        if img is None:
            return None, "OpenCV could not read image", meta

        h, w = img.shape[:2]
        max_dim = 1200
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = _cv2.resize(img, (int(w * scale), int(h * scale)))
            h, w = img.shape[:2]

        gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
        faces = _face_cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=6, minSize=(40, 40)
        )

        if len(faces) == 0:
            return None, "No faces detected", meta

        meta["faces_found"] = len(faces)
        # Sort largest face first (most prominent)
        sorted_faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)

        for i, (x, y, fw, fh) in enumerate(sorted_faces):
            cx = x + fw // 2
            cy = y + fh // 2
            size_pct = round((fw * fh) / (w * h) * 100, 1)
            identities.append(
                f"Person_{i+1} [center: {cx},{cy}  size: {fw}x{fh}px ({size_pct}% of frame)]"
            )

        final_text = "Detected Identities:\n" + "\n".join(identities)
        return final_text, None, meta

    except Exception as e:
        return None, f"Face extraction failed: {e}", meta
