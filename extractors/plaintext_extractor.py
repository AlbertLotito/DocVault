import os


ENCODINGS = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']


def extract(file_path: str) -> tuple:
    """
    Reads a plain-text file and returns its content.
    Tries multiple encodings before giving up.
    """
    print(f"  [text/plain] Reading: {os.path.basename(file_path)}")
    for encoding in ENCODINGS:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            return content, None
        except UnicodeDecodeError:
            continue
        except (OSError, FileNotFoundError) as e:
            return None, f"Could not read file: {e}"
    return None, "Could not decode file with any supported encoding"
