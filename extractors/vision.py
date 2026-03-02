"""
Shared vision model utility for all extractors.
Sends a PIL image to the configured Ollama vision model and returns the response.
"""
import io
import base64
from core.settings import settings


def is_enabled() -> bool:
    v = settings.get('vision:describe_images')
    return str(v).lower() not in ('0', 'false', 'no')


def describe(pil_image, prompt: str = None) -> str:
    """
    Send a PIL image to the Ollama vision model.
    Returns the model's response text, or '' if disabled or on failure.
    """
    if not is_enabled():
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
        print(f"  [vision] {e}")
        return ''
