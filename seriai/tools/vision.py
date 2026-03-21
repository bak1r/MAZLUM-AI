"""
Vision tools — Screenshot capture + Gemini Vision analysis.
macOS native screencapture + Gemini 2.5 Flash Vision API.
"""
import logging
import os
import tempfile
import subprocess
from pathlib import Path

log = logging.getLogger("seriai.tools.vision")


def _capture_screenshot() -> str:
    """Capture screenshot using macOS native screencapture. Returns file path."""
    tmp_path = os.path.join(tempfile.gettempdir(), f"seriai_screenshot_{os.getpid()}_{id(object())}.png")
    try:
        subprocess.run(
            ["screencapture", "-x", "-C", tmp_path],
            timeout=10,
            check=True,
            capture_output=True,
        )
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            return tmp_path
        return ""
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        log.error(f"Screenshot alınamadı: {e}")
        return ""


def _analyze_with_gemini(image_path: str, question: str = "") -> str:
    """Send image to Gemini Vision for analysis."""
    try:
        import google.generativeai as genai
        from PIL import Image
    except ImportError as e:
        return f"Gerekli kütüphane eksik: {e}. pip install google-generativeai Pillow"

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return "GOOGLE_API_KEY veya GEMINI_API_KEY ayarlanmamış."

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-flash")

        img = Image.open(image_path)

        prompt = question if question else (
            "Bu ekran görüntüsünü analiz et. "
            "Ekranda ne var? Açık uygulamalar, içerikler, dikkat çeken şeyler neler? "
            "Türkçe yanıtla, kısa ve öz ol."
        )

        response = model.generate_content(
            [prompt, img],
            generation_config={"max_output_tokens": 1024, "temperature": 0.3},
            request_options={"timeout": 30},
        )

        return response.text if response.text else "Görüntü analiz edilemedi."

    except Exception as e:
        log.error(f"Gemini Vision hatası: {e}")
        return f"Görüntü analiz hatası: {e}"


def analyze_screen(question: str = "") -> dict:
    """
    Capture screenshot and analyze with Vision AI.

    Args:
        question: Optional specific question about the screen

    Returns dict with 'result' or 'error' key.
    """
    screenshot_path = _capture_screenshot()
    if not screenshot_path:
        return {"error": "Ekran görüntüsü alınamadı. macOS screencapture çalışmıyor olabilir."}

    result = _analyze_with_gemini(screenshot_path, question)

    # Cleanup
    try:
        os.remove(screenshot_path)
    except OSError:
        pass

    if result.startswith("Gerekli kütüphane") or result.startswith("GOOGLE_API_KEY"):
        return {"error": result}

    return {"result": result}


def analyze_image(image_path: str, question: str = "") -> dict:
    """
    Analyze any image file with Vision AI.

    Args:
        image_path: Path to the image file
        question: Optional specific question about the image

    Returns dict with 'result' or 'error' key.
    """
    if not os.path.exists(image_path):
        return {"error": f"Dosya bulunamadı: {image_path}"}

    result = _analyze_with_gemini(image_path, question)

    if result.startswith("Gerekli kütüphane") or result.startswith("GOOGLE_API_KEY"):
        return {"error": result}

    return {"result": result}


def register_vision_tools(registry):
    """Register vision tools with the tool registry."""
    from seriai.tools.registry import ToolDef

    registry.register(ToolDef(
        name="screen_check",
        description="Ekran görüntüsü alır ve Vision AI ile analiz eder. 'Ekranda ne var?', 'Ne görüyorsun?' gibi sorulara cevap verir.",
        parameters={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Ekran hakkında spesifik soru (opsiyonel)", "default": ""},
            },
            "required": [],
        },
        handler=analyze_screen,
        domain="desktop",
    ))

    registry.register(ToolDef(
        name="analyze_image",
        description="Herhangi bir resim dosyasını Vision AI ile analiz eder.",
        parameters={
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Resim dosyasının yolu"},
                "question": {"type": "string", "description": "Resim hakkında spesifik soru (opsiyonel)", "default": ""},
            },
            "required": ["image_path"],
        },
        handler=analyze_image,
        domain="desktop",
    ))

    log.info("Vision tools registered (screen_check + analyze_image).")
