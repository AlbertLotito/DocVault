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
    if v is None: return True  # Default to enabled if not explicitly set
    return str(v).lower() not in ('0', 'false', 'no', 'off')


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
        print("  [vision] Stage disabled via settings (vision:describe_images)")
        return ''

    ok, reason = _check_image(pil_image)
    if not ok:
        print(f"  [vision] Image skipped: {reason}")
        return ''

    try:
        import ollama
        from core.monitor import ollama_governor
        
        model = settings.get('vision:model') or 'minicpm-v'
        if prompt is None:
            prompt = (
                'Describe this image in detail. '
                'If the image contains any text, also transcribe it exactly.'
            )
        buf = io.BytesIO()
        pil_image.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode()

        with ollama_governor():
            response = ollama.chat(
                model=model,
                messages=[{'role': 'user', 'content': prompt, 'images': [b64]}],
                options={'temperature': 0},
            )
            res_text = response['message']['content'].strip()
            if not res_text:
                print(f"  [vision] Model '{model}' returned empty response.")
            return res_text

    except Exception as e:
        err = str(e)
        if 'connection' in err.lower():
            print(f"  [vision] Connection error: Is Ollama running? {err}")
        elif '404' in err or 'not found' in err.lower():
            print(f"  [vision] Model error: Have you run 'ollama pull {settings.get('vision:model') or 'minicpm-v'}'?")
        elif 'CUDA error' in err or 'illegal memory access' in err:
            print(f"  [vision:cuda] GPU error — Ollama will recover: {err[:120]}")
        else:
            print(f"  [vision] Unexpected error: {e}")
        return ''
