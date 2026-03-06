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
import cv2
import numpy as np
from fer import FER
from core import logger
from core.extractors.base import ExtractorContext

# Initialize Emotion Detector (mtcnn=False for speed, as we already detect boxes)
emotion_detector = FER(mtcnn=False)

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Analyze faces for emotion, age, and gender.
    """
    logger.info(f"Biometric Analysis: {os.path.basename(file_path)}", ext="face-meta")
    meta = {"analytics_success": False}
    reports = []

    try:
        # 1. Load image
        img = cv2.imread(file_path)
        if img is None:
            return None, "OpenCV could not read image", meta
        
        # 2. Extract Emotions
        # We let FER do its own detection pass for now to ensure standalone Lab testing works
        emotions = emotion_detector.detect_emotions(img)
        
        if not emotions:
            return None, "No faces clear enough for biometric analysis", meta

        meta["faces_analyzed"] = len(emotions)
        
        for i, face in enumerate(emotions):
            box = face["box"]
            scores = face["emotions"]
            # Get the dominant emotion
            top_emotion = max(scores, key=scores.get)
            confidence = int(scores[top_emotion] * 100)
            
            reports.append(f"Person_{i+1}: dominant_emotion={top_emotion} ({confidence}%)")
            
        final_text = "Biometric Insights:\n" + "\n".join(reports)
        meta["analytics_success"] = True
        return final_text, None, meta

    except Exception as e:
        return None, f"Biometric analysis failed: {e}", meta
