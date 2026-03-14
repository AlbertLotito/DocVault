"""
[ PHOTO INTELLIGENCE KERNEL ]
The system's definitive visual memory engine, synthesizing technical EXIF 
metadata with deep semantic scene analysis.

PIPELINE:
1. EXIF Harvesting: Extracts technical specifications including camera make/model, 
   date taken, and GPS coordinates.
2. Coordinate Normalization: Converts raw GPS degree/minute/second data into 
   searchable decimal formats.
3. Semantic Scene Analysis (Vision LLM): Uses Ollama to 'understand' the 
   photograph—identifying subjects, environment, lighting, and mood.
4. Intelligent Synthesis: Combines specs and description into a unified report 
   for the search index.

REQUIRES: Pillow, Ollama (vision:model).
"""

MANIFEST = {
    "id": "com.docvault.vision.photo",
    "version": "1.0.0",
    "name": "Photo Intelligence Engine",
    "extensions": ["jpg", "jpeg", "png", "webp", "heic"],
    "requires": ["pillow", "ollama"]
}

__description__ = (
    "A professional-grade photo analysis engine. It harvests deep technical EXIF "
    "metadata and GPS context while simultaneously performing semantic scene "
    "description via Multi-Modal LLMs, enabling high-resolution visual search."
)

import os
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from core import logger
from core.extractors.base import ExtractorContext
from extractors.vision import describe as vision_describe


def _get_exif_data(image: Image.Image) -> dict:
    """Extract and humanize EXIF tags, ensuring all values are JSON-serializable."""
    exif_data = {}
    try:
        info = image._getexif()
        if info:
            for tag, value in info.items():
                decoded = TAGS.get(tag, tag)
                if decoded == "GPSInfo":
                    gps_data = {}
                    for t in value:
                        sub_tag = GPSTAGS.get(t, t)
                        # Standardise specialized Pillow types to string or float
                        val = value[t]
                        if isinstance(val, (bytes, str, int, float)):
                            gps_data[sub_tag] = val
                        else:
                            gps_data[sub_tag] = str(val)
                    exif_data["GPS"] = gps_data
                else:
                    # Filter for useful tags
                    if decoded in ('Make', 'Model', 'DateTimeOriginal', 'ExposureTime', 'FNumber', 'ISOSpeedRatings', 'FocalLength'):
                        # Standardise specialized Pillow types
                        if isinstance(value, (bytes, str, int, float)):
                            exif_data[decoded] = value
                        else:
                            exif_data[decoded] = str(value)
    except Exception as e:
        logger.debug(f"EXIF extraction failed: {e}", ext="photo-ai")
    return exif_data


def _convert_to_degrees(value) -> float:
    """Helper to convert GPS coordinates to float degrees."""
    d = float(value[0])
    m = float(value[1])
    s = float(value[2])
    return d + (m / 60.0) + (s / 3600.0)


def _format_gps(gps_info: dict) -> str:
    """Convert raw GPS dict into a searchable string."""
    try:
        lat = _convert_to_degrees(gps_info['GPSLatitude'])
        if gps_info['GPSLatitudeRef'] != "N": lat = -lat
        
        lon = _convert_to_degrees(gps_info['GPSLongitude'])
        if gps_info['GPSLongitudeRef'] != "E": lon = -lon
        
        return f"Location: {lat:.6f}, {lon:.6f}"
    except:
        return ""


def extract(file_path: str, ctx: ExtractorContext) -> tuple:
    """
    Multimodal Photo Extraction: EXIF + Vision AI.
    """
    logger.info(f"Analyzing Photo: {os.path.basename(file_path)}", ext="photo-ai")
    meta = {}
    parts = []

    try:
        img = Image.open(file_path)
        
        # 1. Vision AI Analysis
        description = vision_describe(img, prompt=(
            "Analyze this photograph. Identify the primary subject, environment, "
            "lighting, and any notable objects or emotions. Provide a concise "
            "description for a search index."
        ))
        if description:
            parts.append(f"[Visual Content]\n{description}")
            meta["vision_success"] = True

        # 2. Technical Metadata
        exif = _get_exif_data(img)
        if exif:
            spec_lines = []
            if 'Make' in exif or 'Model' in exif:
                spec_lines.append(f"Camera: {exif.get('Make', '')} {exif.get('Model', '')}")
            if 'DateTimeOriginal' in exif:
                spec_lines.append(f"Date Taken: {exif['DateTimeOriginal']}")
            
            if 'GPS' in exif:
                gps_str = _get_gps_str(exif['GPS'])
                if gps_str: spec_lines.append(gps_str)
            
            if spec_lines:
                parts.append("[Technical Specifications]\n" + "\n".join(spec_lines))
            
            meta["exif"] = exif

        final_text = "\n\n".join(parts) if parts else None
        if not final_text:
            return None, "No descriptive or technical content found", meta
            
        return final_text, None, meta

    except Exception as e:
        return None, f"Photo analysis failed: {e}", meta

def _get_gps_str(gps_info: dict) -> str:
    try:
        lat = _convert_to_degrees(gps_info['GPSLatitude'])
        if gps_info.get('GPSLatitudeRef') != "N": lat = -lat
        lon = _convert_to_degrees(gps_info['GPSLongitude'])
        if gps_info.get('GPSLongitudeRef') != "E": lon = -lon
        return f"Coordinates: {lat:.6f}, {lon:.6f}"
    except:
        return ""
