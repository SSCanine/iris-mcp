"""Vision layer: capture + OCR + fuzzy text matching + perceptual hash cache."""

from __future__ import annotations

import difflib
import hashlib
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageOps

from iris.geometry import Rect
from iris.tesseract_bootstrap import configure_tesseract

try:
    import mss

    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    import pytesseract

    HAS_TESSERACT_LIB = True
except ImportError:
    HAS_TESSERACT_LIB = False

try:
    import ctypes

    import win32gui
    import win32ui

    HAS_PRINTWINDOW = True
except ImportError:
    HAS_PRINTWINDOW = False


_TESSERACT_OK = configure_tesseract() if HAS_TESSERACT_LIB else False


# ---------------------------------------------------------------------------
# Capture (mss)
# ---------------------------------------------------------------------------
OPTIMAL_MAX_LONG_EDGE = 1568
OPTIMAL_MAX_PIXELS = 1_150_000
HARD_LIMIT = 8000

_sct = None


def _get_sct():
    global _sct
    if _sct is None:
        if not HAS_MSS:
            raise RuntimeError("mss unavailable")
        _sct = mss.mss()
    return _sct


def capture(bounds: Rect | None = None, monitor: int = 0) -> Image.Image:
    """Capture pixels.

    If bounds given: capture exactly that screen-absolute region.
    Else: capture monitor index (0=virtual all-monitors, 1=primary in mss terms).
    """
    sct = _get_sct()
    if bounds is not None:
        area = {"top": bounds.y, "left": bounds.x, "width": bounds.width, "height": bounds.height}
        raw = sct.grab(area)
    else:
        monitors = sct.monitors
        idx = max(0, min(monitor, len(monitors) - 1))
        raw = sct.grab(monitors[idx])
    return Image.frombytes("RGB", (raw.width, raw.height), raw.rgb)


# PrintWindow flag for DWM-composited windows (Chrome, Electron, modern apps).
# Falls back to plain PrintWindow if the flag fails.
PW_RENDERFULLCONTENT = 0x00000002


def capture_window(hwnd: int) -> Image.Image:
    """Capture a window's contents via Win32 PrintWindow.

    Works on partially-occluded windows because the window itself renders into
    a memory DC, no screen pixels involved. Does NOT work on minimized or
    truly hidden windows (the window has no rendered surface to print).

    Falls back to bounds-based screen capture if PrintWindow is unavailable
    or the returned bitmap is all black (occasional driver issue).

    Args:
        hwnd: Win32 window handle.

    Raises:
        RuntimeError: if hwnd is invalid or has zero size.
    """
    if not HAS_PRINTWINDOW:
        # Fall back to bounds-based capture
        from iris import spatial as _spatial

        info = _spatial._make_window_info(hwnd)
        if info is None:
            raise RuntimeError(f"invalid hwnd {hwnd}")
        return capture(bounds=info.bounds)

    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"invalid hwnd {hwnd}")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError(f"window has invalid dimensions: {width}x{height}")

    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    save_bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()

        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(save_bitmap)

        # PrintWindow with PW_RENDERFULLCONTENT (modern composited content).
        # Returns 1 on success, 0 on failure.
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if not result:
            # Try plain PrintWindow as a fallback.
            result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)

        if not result:
            raise RuntimeError("PrintWindow returned 0")

        bmp_info = save_bitmap.GetInfo()
        bmp_str = save_bitmap.GetBitmapBits(True)

        img = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_str,
            "raw",
            "BGRX",
            0,
            1,
        )

        # Some hardware-accelerated apps (e.g. games, video players) return an
        # all-black bitmap. Detect this and fall back to screen capture.
        if _bitmap_is_black(img):
            from iris import spatial as _spatial

            info = _spatial._make_window_info(hwnd)
            if info is not None:
                return capture(bounds=info.bounds)

        return img
    finally:
        if save_bitmap is not None:
            try:
                win32gui.DeleteObject(save_bitmap.GetHandle())
            except Exception:
                pass
        if save_dc is not None:
            try:
                save_dc.DeleteDC()
            except Exception:
                pass
        if mfc_dc is not None:
            try:
                mfc_dc.DeleteDC()
            except Exception:
                pass
        if hwnd_dc is not None:
            try:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass


def _bitmap_is_black(img: Image.Image, sample_step: int = 32) -> bool:
    """Cheap test: sample pixels on a grid; if every sample is (0,0,0), assume black."""
    w, h = img.width, img.height
    if w == 0 or h == 0:
        return True
    pixels = img.load()
    for y in range(0, h, max(1, h // sample_step)):
        for x in range(0, w, max(1, w // sample_step)):
            p = pixels[x, y]
            if isinstance(p, tuple) and any(v != 0 for v in p[:3]):
                return False
            if isinstance(p, int) and p != 0:
                return False
    return True


def optimal_scale(w: int, h: int) -> float:
    long_scale = OPTIMAL_MAX_LONG_EDGE / max(w, h)
    pixel_scale = math.sqrt(OPTIMAL_MAX_PIXELS / (w * h))
    return min(1.0, long_scale, pixel_scale)


def encode_jpeg(
    img: Image.Image, quality: int = 60, optimize_tokens: bool = True
) -> tuple[bytes, int, int]:
    w, h = img.width, img.height
    if optimize_tokens:
        s = optimal_scale(w, h)
        if s < 1.0:
            w, h = int(w * s), int(h * s)
            img = img.resize((w, h), Image.Resampling.LANCZOS)
    elif max(w, h) > HARD_LIMIT:
        s = HARD_LIMIT / max(w, h)
        w, h = int(w * s), int(h * s)
        img = img.resize((w, h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue(), w, h


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OCRWord:
    text: str
    bbox: Rect  # window-local coords (caller translates to screen-absolute if needed)
    confidence: float

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "confidence": round(self.confidence, 2),
        }


def _preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Grayscale + autocontrast. Upscale 2x if image is small."""
    gray = img.convert("L")
    enhanced = ImageOps.autocontrast(gray, cutoff=2)
    if enhanced.height < 400:
        enhanced = enhanced.resize(
            (enhanced.width * 2, enhanced.height * 2), Image.Resampling.LANCZOS
        )
    return enhanced


def ocr_text(img: Image.Image, preprocess: bool = True) -> list[OCRWord]:
    """Run OCR over the image. Returns word-level results with bboxes in image-local coords.

    If the image was upscaled by preprocessing, bboxes are scaled back to original coords.
    """
    if not (HAS_TESSERACT_LIB and _TESSERACT_OK):
        return []
    if preprocess:
        original_h = img.height
        proc = _preprocess_for_ocr(img)
        scale = original_h / proc.height if proc.height else 1.0
    else:
        proc = img
        scale = 1.0
    try:
        data = pytesseract.image_to_data(proc, output_type=pytesseract.Output.DICT)
    except Exception:
        return []
    out = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if conf < 0:
            continue
        x = int(data["left"][i] * scale)
        y = int(data["top"][i] * scale)
        w = int(data["width"][i] * scale)
        h = int(data["height"][i] * scale)
        out.append(OCRWord(text=text, bbox=Rect(x, y, w, h), confidence=conf / 100.0))
    return out


# Pre-warm Tesseract on import to avoid first-call cold start
def _prewarm_tesseract():
    if not (HAS_TESSERACT_LIB and _TESSERACT_OK):
        return
    try:
        warm_img = Image.new("L", (50, 30), color=255)
        pytesseract.image_to_string(warm_img)
    except Exception:
        pass


_prewarm_tesseract()


# ---------------------------------------------------------------------------
# Fuzzy text find
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TextMatch:
    text: str
    bbox: Rect
    similarity: float
    confidence: float

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "similarity": round(self.similarity, 3),
            "confidence": round(self.confidence, 2),
        }


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def find_text_in_image(
    words: list[OCRWord],
    query: str,
    *,
    fuzzy: bool = True,
    threshold: float = 0.6,
    max_ngram: int = 4,
) -> list[TextMatch]:
    """Search OCR words for query. Tries single words plus n-gram joins.

    Returns matches sorted by similarity descending.
    """
    if not query or not words:
        return []
    q = query.strip()
    out: list[TextMatch] = []
    # Single words
    for w in words:
        sim = _similarity(w.text, q)
        if not fuzzy and sim < 1.0:
            continue
        if sim >= threshold:
            out.append(TextMatch(text=w.text, bbox=w.bbox, similarity=sim, confidence=w.confidence))
    # N-grams (joined text)
    if " " in q or len(q.split()) > 1:
        max_n = min(max_ngram, len(q.split()) + 1, len(words))
        for n in range(2, max_n + 1):
            for i in range(len(words) - n + 1):
                window = words[i : i + n]
                joined = " ".join(w.text for w in window)
                sim = _similarity(joined, q)
                if not fuzzy and sim < 1.0:
                    continue
                if sim >= threshold:
                    # Bbox = bounding rect over all words in window
                    x = min(w.bbox.left for w in window)
                    y = min(w.bbox.top for w in window)
                    r = max(w.bbox.right for w in window)
                    b = max(w.bbox.bottom for w in window)
                    avg_conf = sum(w.confidence for w in window) / n
                    out.append(
                        TextMatch(
                            text=joined,
                            bbox=Rect.from_ltrb(x, y, r, b),
                            similarity=sim,
                            confidence=avg_conf,
                        )
                    )
    out.sort(key=lambda m: m.similarity, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Perceptual hash + OCR cache
# ---------------------------------------------------------------------------
def phash(img: Image.Image) -> str:
    """64-bit perceptual hash via 8x8 grayscale + threshold against mean."""
    small = img.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / len(pixels)
    bits = "".join("1" if p > avg else "0" for p in pixels)
    return hashlib.sha1(bits.encode("ascii")).hexdigest()[:16]


# Per-token OCR cache: { token_id -> (phash, [OCRWord]) }
_OCR_CACHE: dict[str, tuple[str, list[OCRWord]]] = {}
_OCR_CACHE_HITS = 0
_OCR_CACHE_MISSES = 0


def cached_ocr(token_id: str, img: Image.Image) -> list[OCRWord]:
    """OCR with caching keyed on (token_id, perceptual hash of pixels)."""
    global _OCR_CACHE_HITS, _OCR_CACHE_MISSES
    h = phash(img)
    cached = _OCR_CACHE.get(token_id)
    if cached and cached[0] == h:
        _OCR_CACHE_HITS += 1
        return cached[1]
    _OCR_CACHE_MISSES += 1
    words = ocr_text(img)
    _OCR_CACHE[token_id] = (h, words)
    return words


def ocr_cache_stats() -> dict:
    total = _OCR_CACHE_HITS + _OCR_CACHE_MISSES
    return {
        "hits": _OCR_CACHE_HITS,
        "misses": _OCR_CACHE_MISSES,
        "hit_rate": (_OCR_CACHE_HITS / total) if total else 0.0,
        "tokens_cached": len(_OCR_CACHE),
    }


def clear_ocr_cache(token_id: str | None = None) -> None:
    if token_id is None:
        _OCR_CACHE.clear()
    else:
        _OCR_CACHE.pop(token_id, None)
