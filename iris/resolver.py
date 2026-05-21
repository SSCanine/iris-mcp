"""Resolver: route find/click requests to the right backend (UIA -> OCR -> handoff)."""
from __future__ import annotations
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from iris.geometry import Rect
from iris.tokens import FocusToken, inspect as token_inspect
from iris import spatial as spatial_mod
from iris import semantic as semantic_mod
from iris import vision as vision_mod


# Token-overlap ranking helpers. Used to surface candidates that share
# concept words with the target even when raw edit-distance is low. Example:
# target='Mic/Aux' vs candidate='GoXLR Mic' shares 'mic' -> meaningful boost.
_TOKEN_SPLIT_RE = re.compile(r"[\s/\-_.,;:|\\()\[\]]+")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "this", "that",
    "you", "your", "are", "was", "but", "not", "any", "all",
})


def _tokenize(s: str) -> set[str]:
    """Lowercase tokens of length>2 with stopwords stripped."""
    if not s:
        return set()
    toks = _TOKEN_SPLIT_RE.split(s.lower())
    return {t for t in toks if len(t) > 2 and t not in _STOPWORDS}


def _token_overlap(target: str, candidate: str) -> float:
    """Fraction of target tokens that appear in candidate. 0.0..1.0."""
    t = _tokenize(target)
    if not t:
        return 0.0
    c = _tokenize(candidate)
    if not c:
        return 0.0
    return len(t & c) / len(t)


@dataclass
class FindResult:
    found: bool
    backend: str               # 'uia' | 'ocr' | 'vision_handoff'
    hits: list[dict] = field(default_factory=list)
    nearest_matches: list[dict] = field(default_factory=list)
    screenshot: Optional[bytes] = None
    screenshot_dims: Optional[tuple[int, int]] = None
    backends_tried: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    notes: list[str] = field(default_factory=list)
    drift_detected: bool = False
    drift_summary: Optional[dict] = None
    # Independent state flags so the caller can self-correct off-screen clicks.
    # See plan 2026-04-27-iris-v2-phase15.
    window_state: Optional[dict] = None

    def to_dict(self) -> dict:
        d = {
            "found": self.found,
            "backend": self.backend,
            "hits": self.hits,
            "backends_tried": self.backends_tried,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "notes": self.notes,
        }
        if self.nearest_matches:
            d["nearest_matches"] = self.nearest_matches
        if self.screenshot is not None:
            d["screenshot_bytes_len"] = len(self.screenshot)
            d["screenshot_dims"] = list(self.screenshot_dims or ())
        if self.drift_detected:
            d["drift_detected"] = True
            d["drift_summary"] = self.drift_summary
        if self.window_state is not None:
            d["window_state"] = self.window_state
        return d


def _capture_window(token: FocusToken) -> Optional[object]:
    """Capture only the token's window. Returns PIL Image or None.

    Uses Win32 PrintWindow so the capture is the actual window contents even
    when the window is occluded by other windows. Falls back to bounds-based
    capture if PrintWindow fails (e.g. for hardware-accelerated apps).
    """
    try:
        return vision_mod.capture_window(token.hwnd)
    except Exception:
        try:
            return vision_mod.capture(bounds=token.bounds_at_creation)
        except Exception:
            return None


def find(
    token: FocusToken,
    target: str,
    *,
    fuzzy: bool = True,
    threshold: float = 0.6,
    capture_for_handoff: bool = True,
) -> FindResult:
    """Find target in the focused window. Tries UIA, falls back to OCR, falls back to vision handoff."""
    t0 = time.perf_counter()
    result = FindResult(found=False, backend="vision_handoff")

    # Snapshot window state once so every return path includes it. Lets the
    # caller (Claude) detect off-screen clicks before issuing them.
    try:
        snap = token_inspect(token)
        if snap.get("alive", True):
            result.window_state = {
                "minimized": bool(snap.get("minimized", False)),
                "occluded": bool(snap.get("occluded", False)),
                "off_screen": bool(snap.get("off_screen", False)),
            }
    except Exception:
        result.window_state = None

    # 1. Try UIA
    if semantic_mod.HAS_UIA:
        result.backends_tried.append("uia")
        if semantic_mod.supports_uia(token.hwnd, token.pid):
            controls = semantic_mod.query(token.hwnd, name=target, max_results=10)
            if controls:
                result.found = True
                result.backend = "uia"
                result.hits = [c.to_dict() for c in controls]
                result.elapsed_ms = (time.perf_counter() - t0) * 1000
                return result

    # 2. Try OCR
    result.backends_tried.append("ocr")
    img = _capture_window(token)
    occlusion_retried = False
    if img is not None:
        words = vision_mod.cached_ocr(token.id, img)
        matches = vision_mod.find_text_in_image(words, target, fuzzy=fuzzy, threshold=threshold)
        # If OCR found nothing AND window is occluded, raise and retry once
        if not matches:
            try:
                if spatial_mod.is_occluded(token.hwnd):
                    occlusion_retried = True
                    spatial_mod.bring_to_front(token.hwnd)
                    import time as _t
                    _t.sleep(0.2)
                    vision_mod.clear_ocr_cache(token.id)
                    img = _capture_window(token)
                    if img is not None:
                        words = vision_mod.cached_ocr(token.id, img)
                        matches = vision_mod.find_text_in_image(
                            words, target, fuzzy=fuzzy, threshold=threshold,
                        )
                        if occlusion_retried:
                            result.notes.append("occlusion_retry: window raised, OCR re-run")
            except Exception as e:
                result.notes.append(f"occlusion_retry_failed:{e}")
        if matches:
            # Translate window-local bbox to screen-absolute by adding window origin
            origin_x = token.bounds_at_creation.x
            origin_y = token.bounds_at_creation.y
            hits = []
            for m in matches:
                d = m.to_dict()
                d["bbox"]["x"] += origin_x
                d["bbox"]["y"] += origin_y
                hits.append(d)
            result.found = True
            result.backend = "ocr"
            result.hits = hits
            result.elapsed_ms = (time.perf_counter() - t0) * 1000
            return result
        # Capture nearest matches at lower threshold for the handoff. Re-rank
        # so candidates sharing concept words with the target (e.g. 'Mic/Aux'
        # vs 'GoXLR Mic' both contain 'mic') float to the top, instead of
        # ranking purely by edit-distance.
        soft = vision_mod.find_text_in_image(words, target, fuzzy=True, threshold=0.4)
        if soft:
            origin_x = token.bounds_at_creation.x
            origin_y = token.bounds_at_creation.y
            scored = []
            for m in soft:
                d = m.to_dict()
                d["bbox"]["x"] += origin_x
                d["bbox"]["y"] += origin_y
                d["backend"] = "ocr"
                d["token_overlap"] = round(_token_overlap(target, m.text), 3)
                scored.append(d)
            scored.sort(
                key=lambda c: (c["token_overlap"], c.get("similarity", 0)),
                reverse=True,
            )
            result.nearest_matches = scored[:5]

    # 3. Vision handoff: include screenshot + nearest matches so Claude can decide
    if capture_for_handoff and img is not None:
        try:
            data, w, h = vision_mod.encode_jpeg(img, quality=60, optimize_tokens=True)
            result.screenshot = data
            result.screenshot_dims = (w, h)
        except Exception as e:
            result.notes.append(f"screenshot_encode_failed:{e}")

    result.notes.append(
        f"No exact match for {target!r}. "
        f"Window may have changed structure since the target was last seen."
    )
    result.elapsed_ms = (time.perf_counter() - t0) * 1000
    return result


def suggest_alternatives(token: FocusToken, target: str, *, top_n: int = 10) -> dict:
    """Lower-threshold fuzzy search across BOTH UIA and OCR."""
    t0 = time.perf_counter()
    candidates = []
    # UIA: dump all controls, score against target
    if semantic_mod.HAS_UIA and semantic_mod.supports_uia(token.hwnd, token.pid):
        dump = semantic_mod.walk_tree(token.hwnd, max_depth=6, max_nodes=200)
        target_l = target.lower()
        import difflib
        for node in dump:
            name = node.get("name", "")
            if not name:
                continue
            sim = difflib.SequenceMatcher(None, name.lower(), target_l).ratio()
            if sim >= 0.4:
                candidates.append({
                    "text": name,
                    "role": node.get("role"),
                    "bbox": node.get("bounds"),
                    "similarity": round(sim, 3),
                    "backend": "uia",
                })
    # OCR
    img = _capture_window(token)
    if img is not None:
        words = vision_mod.cached_ocr(token.id, img)
        soft = vision_mod.find_text_in_image(words, target, fuzzy=True, threshold=0.4)
        ox = token.bounds_at_creation.x
        oy = token.bounds_at_creation.y
        for m in soft:
            d = m.to_dict()
            d["bbox"]["x"] += ox
            d["bbox"]["y"] += oy
            d["backend"] = "ocr"
            candidates.append(d)
    # Rank: similarity*0.55 + prominence*0.20 + token_overlap*0.25
    # Token overlap rewards candidates that share concept words with the
    # target ("Mic/Aux" vs "GoXLR Mic" share 'mic'), which pure edit-distance
    # otherwise ranks poorly because the surrounding characters differ.
    win_area = max(token.bounds_at_creation.area, 1)
    for c in candidates:
        b = c.get("bbox") or {}
        area = max(int(b.get("width", 0)) * int(b.get("height", 0)), 0)
        prominence = min(area / win_area, 1.0)
        overlap = _token_overlap(target, c.get("text", ""))
        c["token_overlap"] = round(overlap, 3)
        c["score"] = round(
            c["similarity"] * 0.55 + prominence * 0.20 + overlap * 0.25, 3,
        )
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {
        "target": target,
        "candidates": candidates[:top_n],
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
