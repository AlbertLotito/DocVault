"""
Shared vision model utility for all extractors.
Sends a PIL image to the configured Ollama vision model and returns the response.
"""
import io
import base64
from core.settings import settings

# Images smaller than this in either dimension are skipped (px)
_MIN_DIMENSION = 64
# Images with aspect ratio (long / short) above this are skipped
_MAX_ASPECT_RATIO = 10.0


def is_enabled() -> bool:
    v = settings.get('vision:describe_images')
    return str(v).lower() not in ('0', 'false', 'no')


def _check_image(pil_image) -> tuple:
    """Return (ok: bool, reason: str). False means skip before vision call."""
    w, h = pil_image.size
    if w < _MIN_DIMENSION or h < _MIN_DIMENSION:
        return False, f"too small ({w}x{h})"
    ratio = max(w, h) / min(w, h)
    if ratio > _MAX_ASPECT_RATIO:
        return False, f"extreme aspect ratio ({w}x{h}, ratio {ratio:.1f}:1)"
    return True, ''


def describe(pil_image, prompt: str = None) -> str:
    """
    Send a PIL image to the Ollama vision model.
    Returns the model's response text, or '' if disabled, skipped, or on failure.
    """
    if not is_enabled():
        return ''

    ok, reason = _check_image(pil_image)
    if not ok:
        print(f"  [vision] Skipped: {reason}")
        return ''

    try:
        import ollama
        model = settings.get('vision:model') or 'minicpm-v'
        if prompt is None:
            prompt = (
                'Describe this image in detail. '
                'If the image contains any text, also transcribe it exactly.'
            )
        buf = io.BytesIO()
        pil_image.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()
        response = ollama.chat(
            model=model,
            messages=[{'role': 'user', 'content': prompt, 'images': [b64]}],
            options={'temperature': 0},
        )
        return response['message']['content'].strip()
    except Exception as e:
        err = str(e)
        if 'CUDA error' in err or 'illegal memory access' in err:
            print(f"  [vision:cuda] GPU error — Ollama will recover: {err[:120]}")
        else:
            print(f"  [vision] {e}")
        return ''
