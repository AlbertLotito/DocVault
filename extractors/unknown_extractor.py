import os
def extract(file_path):
    ext = os.path.splitext(file_path)[1]
    return None, f"UNKNOWN file type: {ext} — no extractor registered"
