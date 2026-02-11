# app/services/footnotes.py
"""
Extract and validate footnotes, detect unusual colored text, and flag existing highlights in PDFs.
"""
import re
from typing import Dict, List, Optional, Tuple, Any

# PyMuPDF annotation type for highlight (PDF_ANNOT_HIGHLIGHT = 8)
PDF_ANNOT_HIGHLIGHT = 8


def extract_footnotes_from_pdf(pdf_bytes: bytes) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """
    Extract the single universal footnote section for the entire document.
    There is at most one set of footnotes (or none); they may appear at the end of the
    document or in a dedicated section. All footnote references in the document refer
    to this one dictionary.

    Returns:
        (footnotes dict, locations dict)
        - footnotes: { "1": "footnote text", "2": "...", "*": "..." }
        - locations: ref -> {"page": int, "bbox": [x0, y0, x1, y1]} for first line of each footnote (for highlighting)
    """
    import fitz
    result: Dict[str, str] = {}
    locations: Dict[str, Dict[str, Any]] = {}
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_height = page.rect.height
            footnote_y_threshold = page_height * 0.75
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                if block.get("type") != 0:
                    continue
                bbox = block.get("bbox", (0, 0, 0, 0))
                y0 = bbox[1]
                if y0 < footnote_y_threshold:
                    continue
                for line in block.get("lines", []):
                    line_text_parts = []
                    line_bbox = None
                    for span in line.get("spans", []):
                        line_text_parts.append(span.get("text", ""))
                        if line_bbox is None and span.get("bbox"):
                            line_bbox = list(span["bbox"])
                    line_text = "".join(line_text_parts).strip()
                    if not line_text or len(line_text) < 2:
                        continue
                    num_match = re.match(r"^(\d+)[\.\)\s]+(.+)$", line_text)
                    aster_match = re.match(r"^(\*+)\s+(.+)$", line_text)
                    if num_match:
                        key = num_match.group(1).strip()
                        text = num_match.group(2).strip()
                        if key and text:
                            result[key] = text
                            if line_bbox is not None:
                                locations[key] = {"page": page_num + 1, "bbox": line_bbox}
                    elif aster_match:
                        key = aster_match.group(1).strip()
                        text = aster_match.group(2).strip()
                        if key and text:
                            result[key] = text
                            if line_bbox is not None:
                                locations[key] = {"page": page_num + 1, "bbox": line_bbox}
                    elif result:
                        last_key = list(result.keys())[-1]
                        result[last_key] = result[last_key] + " " + line_text
        doc.close()
    except Exception:
        pass
    return result, locations


def _span_is_footnote_ref(span_text: str) -> Optional[str]:
    """
    Treat a span as a footnote reference if its entire text is a number or asterisks.
    This catches superscript/subscript refs (typically in a separate small span).
    Returns the reference string (e.g. "1", "*") or None.
    """
    t = span_text.strip()
    if not t:
        return None
    if re.fullmatch(r"\d+", t):
        return t
    if re.fullmatch(r"\*+", t):
        return t
    return None


def check_footnote_references(
    pdf_bytes: bytes,
    footnotes: Dict[str, str],
) -> List[Dict[str, Any]]:
    """
    For each page, find footnote references in body text (numbers, asterisks).
    Refs are often in smaller or superscript spans; we treat a span as a ref when
    its entire text is a number or asterisks, so we don't miss small/superscript refs.
    Check they point to the document's universal footnote dictionary.

    Returns:
        List of issues: { "page": int, "issue_type": str, "message": str, "reference": str, "bbox": [x0,y0,x1,y1]? }
    """
    import fitz
    issues = []
    if not footnotes:
        return issues
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_no = page_num + 1
            blocks = page.get_text("dict").get("blocks", [])
            page_height = page.rect.height
            footnote_y_threshold = page_height * 0.75
            seen_refs_this_page: set = set()
            for block in blocks:
                if block.get("type") != 0:
                    continue
                bbox = block.get("bbox", (0, 0, 0, 0))
                if bbox[1] >= footnote_y_threshold:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        ref = _span_is_footnote_ref(text)
                        if ref is None:
                            continue
                        if ref in seen_refs_this_page:
                            continue
                        seen_refs_this_page.add(ref)
                        if ref not in footnotes:
                            span_bbox = span.get("bbox")
                            issues.append({
                                "page": page_no,
                                "issue_type": "footnote_reference_missing",
                                "message": f"Footnote reference '{ref}' has no matching footnote in this document.",
                                "reference": ref,
                                "bbox": list(span_bbox) if span_bbox else None,
                            })
        doc.close()
    except Exception:
        pass
    return issues


def find_ref_bbox_on_page(
    pdf_bytes: bytes,
    page_1based: int,
    ref_text: str,
) -> Optional[List[float]]:
    """
    Find the bbox of a footnote reference on a page. Looks for spans whose text matches ref_text
    and whose font size is smaller than the page median (superscript/small refs).
    Returns [x0, y0, x1, y1] or None.
    """
    import fitz
    if not ref_text or not pdf_bytes:
        return None
    ref_text = ref_text.strip()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        page_idx = page_1based - 1
        if page_idx < 0 or page_idx >= len(doc):
            doc.close()
            return None
        page = doc[page_idx]
        page_height = page.rect.height
        footnote_y_threshold = page_height * 0.75
        blocks = page.get_text("dict").get("blocks", [])
        sizes = []
        candidate_bbox = None
        for block in blocks:
            if block.get("type") != 0:
                continue
            bbox = block.get("bbox", (0, 0, 0, 0))
            if bbox[1] >= footnote_y_threshold:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    size = span.get("size")
                    if size is not None:
                        sizes.append(float(size))
                    if text == ref_text:
                        span_bbox = span.get("bbox")
                        if span_bbox:
                            candidate_bbox = list(span_bbox)
                            break
                if candidate_bbox is not None:
                    break
            if candidate_bbox is not None:
                break
        if candidate_bbox is not None:
            doc.close()
            return candidate_bbox
        # If no exact match, try matching with size heuristic: prefer small spans (superscript)
        if sizes:
            import statistics
            median_size = statistics.median(sizes)
            small_threshold = median_size * 0.92
        else:
            small_threshold = None
        for block in blocks:
            if block.get("type") != 0:
                continue
            bbox = block.get("bbox", (0, 0, 0, 0))
            if bbox[1] >= footnote_y_threshold:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    size = span.get("size")
                    if text == ref_text:
                        span_bbox = span.get("bbox")
                        if span_bbox:
                            if small_threshold is None or (size is not None and float(size) <= small_threshold):
                                doc.close()
                                return list(span_bbox)
                            if candidate_bbox is None:
                                candidate_bbox = list(span_bbox)
                if candidate_bbox is not None:
                    break
            if candidate_bbox is not None:
                break
        doc.close()
        return list(candidate_bbox) if candidate_bbox else None
    except Exception:
        return None


def get_unusual_colored_text(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Flag only red text (edit remnants like track-changes or comments). Ignore bold, blue headings, etc.
    
    Returns:
        List of { "page": int, "text": str, "color_hex": str, "bbox": tuple } for red text only.
    """
    import fitz
    NORMAL_COLOR = 0
    issues = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        color = span.get("color")
                        text = (span.get("text") or "").strip()
                        if not text or len(text) < 2:
                            continue
                        if color is None or color == NORMAL_COLOR:
                            continue
                        if not isinstance(color, int) or color == 0:
                            continue
                        r = (color >> 16) & 0xFF
                        g = (color >> 8) & 0xFF
                        b = color & 0xFF
                        # Only flag red / red-orange (edit remnants): R dominant, G and B low
                        if r >= 100 and g <= 120 and b <= 120 and (r > g or r > b):
                            color_hex = f"#{r:02x}{g:02x}{b:02x}"
                            issues.append({
                                "page": page_num + 1,
                                "text": text[:100],
                                "color_hex": color_hex,
                                "bbox": tuple(span.get("bbox", (0, 0, 0, 0))),
                            })
        doc.close()
    except Exception:
        pass
    return issues


def get_existing_highlights(pdf_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Find existing highlight annotations in the PDF (left by previous reviewer or draft).
    
    Returns:
        List of { "page": int, "message": str, "rect": list }
    """
    import fitz
    results = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            for annot in page.annots() or []:
                try:
                    atype = getattr(annot, "type", None)
                    is_highlight = atype and (atype[0] == PDF_ANNOT_HIGHLIGHT if isinstance(atype, (list, tuple)) else atype == PDF_ANNOT_HIGHLIGHT)
                    if is_highlight:
                        rect = annot.rect
                        results.append({
                            "page": page_num + 1,
                            "message": "Existing highlight found in document (review or remove before finalising).",
                            "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                        })
                except Exception:
                    continue
        doc.close()
    except Exception:
        pass
    return results


def run_footnote_and_formatting_checks(pdf_bytes: bytes) -> Tuple[
    Dict[str, str],
    Dict[str, Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """
    Run all footnote and formatting checks.

    Returns:
        (footnotes dict, footnote_locations ref->{page,bbox}, footnote_issues, unusual_color_issues, existing_highlight_issues)
    """
    footnotes, footnote_locations = extract_footnotes_from_pdf(pdf_bytes)
    footnote_issues = check_footnote_references(pdf_bytes, footnotes)
    unusual_color_issues = get_unusual_colored_text(pdf_bytes)
    existing_highlight_issues = get_existing_highlights(pdf_bytes)
    return footnotes, footnote_locations, footnote_issues, unusual_color_issues, existing_highlight_issues
