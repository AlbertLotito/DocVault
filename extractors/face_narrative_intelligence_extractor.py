"""
[ FACE NARRATIVE INTELLIGENCE KERNEL ]
Tier 3 engine for social context and interaction analysis.

PIPELINE:
1. Social Scene Analysis (Vision LLM): Uses a Multi-Modal LLM to 'read' the 
   dynamic between the people in the image.
2. Interaction Mapping: Identifies activities, social settings, and 
   implied relationships (e.g., "A formal business meeting", "Friends celebrating").
3. Narrative Synthesis: Generates a descriptive report focused on the 
   human story within the frame.
4. Semantic Search Synthesis: Injects rich social context into the index, 
   enabling search for events and interpersonal dynamics.

REQUIRES: Ollama (vision:model).
"""

MANIFEST = {
    "id": "com.docvault.vision.face.narrative",
    "version": "1.0.0",
    "name": "Face Narrative Intelligence",
    "extensions": ["jpg", "jpeg", "png", "webp"],
    "requires": ["ollama"]
}

__description__ = (
    "The 'Social Context' engine. It utilizes Vision AI to analyze human "
    "interactions and environmental relationships, providing a narrative "
    "summary of what people are doing and how they are engaging with each other."
)

import os
from PIL import Image
from core import logger
from core.extractors.base import ExtractorContext
from extractors.vision import describe as vision_describe

def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Generate a narrative report of human interactions using Vision AI.
    """
    logger.info(f"Narrative Analysis: {os.path.basename(file_path)}", ext="face-story")
    meta = {"narrative_success": False}

    try:
        # 1. Load image for Vision pass
        img = Image.open(file_path)
        
        # 2. Vision AI Analysis with specialized 'Social' prompt
        description = vision_describe(img, prompt=(
            "Analyze the people in this image. Describe their interaction, "
            "their relationship to the environment, and any social or narrative "
            "context (e.g., 'A family dinner', 'Colleagues in a meeting'). "
            "Focus on the social dynamic and what they are doing."
        ))
        
        if not description:
            return None, "No social narrative could be generated", meta

        report = [
            "[ SOCIAL NARRATIVE ]",
            description
        ]
        
        final_text = "\n".join(report)
        meta["narrative_success"] = True
        return final_text, None, meta

    except Exception as e:
        return None, f"Narrative analysis failed: {e}", meta
