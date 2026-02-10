# app/services/annotate.py
import fitz  # PyMuPDF
import io
import re
from typing import List, Tuple, Optional, Dict
from app.models import AnalysisResult, MissingPhrase, DetectedDisclaimer
# Removed recommendations - focusing on disclaimer highlighting


class AnnotationInfo:
    """Information about an annotation to add to PDF."""
    def __init__(self, page_num: int, text: str, annotation_type: str, message: str, bbox: Tuple[float, float, float, float]):
        self.page_num = page_num
        self.text = text
        self.annotation_type = annotation_type  # 'highlight', 'note', 'strikeout'
        self.message = message
        self.bbox = bbox  # (x0, y0, x1, y1)


def find_text_in_pdf_with_fallbacks(
    pdf_doc: fitz.Document,
    exact_text: Optional[str],
    keyword_fallback: Optional[str] = None,
) -> List[Tuple[int, fitz.Rect]]:
    """
    Find text in PDF using exact quote first, then progressively shorter substrings, then keyword.
    Returns list of (page_num, rect) for highlighting so comments map to the right line.
    """
    if exact_text and len(exact_text.strip()) >= 3:
        # Try exact first
        results = find_text_in_pdf(pdf_doc, exact_text.strip())
        if results:
            return results
        # Try first 80 chars (PDF may have different spacing)
        if len(exact_text) > 80:
            results = find_text_in_pdf(pdf_doc, exact_text[:80].strip())
            if results:
                return results
        if len(exact_text) > 40:
            results = find_text_in_pdf(pdf_doc, exact_text[:40].strip())
            if results:
                return results
        if len(exact_text) > 20:
            results = find_text_in_pdf(pdf_doc, exact_text[:20].strip())
            if results:
                return results
    if keyword_fallback and len(keyword_fallback.strip()) >= 2:
        return find_text_in_pdf(pdf_doc, keyword_fallback.strip())
    return []


def add_highlight_with_popup(
    page: "fitz.Page",
    rect: "fitz.Rect",
    message: str,
    stroke_color: Tuple[float, float, float],
    opacity: float = 0.5,
    popup_title: Optional[str] = None,
) -> None:
    """
    Add a highlight annotation and a sticky-note (text) annotation so the message
    appears in a popup when the user clicks the note or the highlight.
    """
    highlight = page.add_highlight_annot(rect)
    highlight.set_colors(stroke=stroke_color)
    highlight.set_info(content=message)
    highlight.set_opacity(opacity)
    highlight.update()
    # Sticky note so the popup is visible (comment icon that opens to show message)
    msg_short = (message or "")[:200]
    title = (popup_title or message or "Note")[:60]
    note_y = max(20, rect.y0 - 22)
    note_point = fitz.Point(rect.x0, note_y)
    try:
        note = page.add_text_annot(note_point, title)
        note.set_info(content=msg_short)
        note.update()
    except Exception:
        pass


def find_text_in_pdf(pdf_doc: fitz.Document, search_text: str) -> List[Tuple[int, fitz.Rect]]:
    """
    Find all occurrences of text in the PDF and return page numbers and bounding boxes.
    Uses multiple search strategies for better text matching.
    
    Args:
        pdf_doc: PyMuPDF document
        search_text: Text to search for
        
    Returns:
        List of (page_num, rect) tuples
    """
    results = []
    seen_rects = set()  # Avoid duplicates
    
    # Clean search text
    search_text = search_text.strip()
    if not search_text or len(search_text) < 3:
        return results
    
    for page_num in range(len(pdf_doc)):
        page = pdf_doc[page_num]
        
        # Strategy 1: Try exact match
        text_instances = page.search_for(search_text)
        for rect in text_instances:
            rect_key = (page_num, round(rect.x0, 1), round(rect.y0, 1))
            if rect_key not in seen_rects:
                results.append((page_num, rect))
                seen_rects.add(rect_key)
        
        # Strategy 2: Try case-insensitive
        if search_text != search_text.lower():
            text_instances_lower = page.search_for(search_text.lower())
            for rect in text_instances_lower:
                rect_key = (page_num, round(rect.x0, 1), round(rect.y0, 1))
                if rect_key not in seen_rects:
                    results.append((page_num, rect))
                    seen_rects.add(rect_key)
        
        # Strategy 3: Try first 50 chars if text is long
        if len(search_text) > 50:
            short_text = search_text[:50]
            short_instances = page.search_for(short_text)
            for rect in short_instances:
                rect_key = (page_num, round(rect.x0, 1), round(rect.y0, 1))
                if rect_key not in seen_rects:
                    results.append((page_num, rect))
                    seen_rects.add(rect_key)
        
        # Strategy 4: Try sentence-by-sentence for long text
        if len(search_text.split('.')) > 1:
            sentences = [s.strip() for s in search_text.split('.') if len(s.strip()) > 20]
            for sentence in sentences[:2]:  # First 2 sentences
                sent_instances = page.search_for(sentence)
                for rect in sent_instances:
                    rect_key = (page_num, round(rect.x0, 1), round(rect.y0, 1))
                    if rect_key not in seen_rects:
                        results.append((page_num, rect))
                        seen_rects.add(rect_key)
    
    return results


def parse_llm_suggestions(llm_text: str) -> List[dict]:
    """
    Parse LLM suggestions text into structured issues.
    
    Args:
        llm_text: LLM suggestions as text
        
    Returns:
        List of dicts with 'text', 'suggestion', 'type' keys
    """
    issues = []
    
    if not llm_text or "No issues found" in llm_text or "compliant" in llm_text.lower():
        return issues
    
    # Split by ISSUE pattern
    issue_pattern = r'ISSUE\s+\d+:'
    issue_sections = re.split(issue_pattern, llm_text, flags=re.IGNORECASE)
    
    for section in issue_sections[1:]:  # Skip first part (before first ISSUE)
        issue = {}
        
        # Extract TEXT
        text_match = re.search(r'TEXT:\s*(.+?)(?:\n|SUGGESTION:)', section, re.DOTALL | re.IGNORECASE)
        if text_match:
            issue["text"] = text_match.group(1).strip()
        else:
            issue["text"] = "MISSING"
        
        # Extract SUGGESTION
        suggestion_match = re.search(r'SUGGESTION:\s*(.+?)(?:\n|TYPE:)', section, re.DOTALL | re.IGNORECASE)
        if suggestion_match:
            issue["suggestion"] = suggestion_match.group(1).strip()
        else:
            # Try to get everything after SUGGESTION:
            suggestion_match = re.search(r'SUGGESTION:\s*(.+?)(?:\n\n|\Z)', section, re.DOTALL | re.IGNORECASE)
            if suggestion_match:
                issue["suggestion"] = suggestion_match.group(1).strip()
            else:
                continue  # Skip if no suggestion
        
        # Extract TYPE
        type_match = re.search(r'TYPE:\s*(\S+)', section, re.IGNORECASE)
        if type_match:
            issue["type"] = type_match.group(1).strip()
        else:
            issue["type"] = "general"
        
        issues.append(issue)
    
    # If no structured format found, try to extract suggestions from paragraphs
    if not issues:
        # Look for numbered lists or bullet points
        lines = llm_text.split('\n')
        current_suggestion = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # Check if line starts with number or bullet
            if re.match(r'^\d+[\.\)]', line) or line.startswith('-') or line.startswith('•'):
                if current_suggestion:
                    issues.append({
                        "text": "MISSING",
                        "suggestion": current_suggestion,
                        "type": "general"
                    })
                current_suggestion = line.lstrip('0123456789.-)• ').strip()
            elif current_suggestion and len(line) > 20:
                current_suggestion += " " + line
        
        if current_suggestion:
            issues.append({
                "text": "MISSING",
                "suggestion": current_suggestion,
                "type": "general"
            })
    
    return issues


def find_disclaimer_section(pdf_doc: fitz.Document, detected_text: str) -> List[Tuple[int, fitz.Rect]]:
    """
    Find the full disclaimer section in the PDF by searching for key phrases from detected text.
    
    Args:
        pdf_doc: PyMuPDF document
        detected_text: Detected disclaimer text
        
    Returns:
        List of (page_num, rect) tuples covering the full disclaimer area
    """
    results = []
    
    # Search for jurisdiction-specific phrases first
    jurisdiction_phrases = [
        "for residents of the united arab emirates",
        "for residents of the uae",
        "for residents of the state of kuwait",
        "for residents of the state of qatar",
        "for residents of the sultanate of oman",
        "for residents of the kingdom of saudi arabia",
        "for residents of the dubai international financial centre",
        "for residents of the difc"
    ]
    
    for page_num in range(len(pdf_doc)):
        page = pdf_doc[page_num]
        page_text = page.get_text().lower()
        
        # Search for jurisdiction phrases
        for phrase in jurisdiction_phrases:
            if phrase in page_text:
                # Find the text block containing this phrase
                text_blocks = page.get_text("blocks")
                for block in text_blocks:
                    if phrase in block[4].lower() if len(block) > 4 else False:
                        rect = fitz.Rect(block[0], block[1], block[2], block[3])
                        results.append((page_num, rect))
        
        # Search for "Disclaimers" or "Disclaimer" heading
        for keyword in ['disclaimers', 'disclaimer']:
            instances = page.search_for(keyword)
            instances_lower = page.search_for(keyword.lower())
            for rect in instances + instances_lower:
                results.append((page_num, rect))
        
        # Try to find key sentences from detected text
        if detected_text:
            # Use first 50-100 chars as search key
            search_key = detected_text[:100].strip()
            if len(search_key) > 30:
                instances = page.search_for(search_key)
                if instances:
                    for rect in instances:
                        results.append((page_num, rect))
                else:
                    # Try shorter key
                    short_key = search_key[:50]
                    instances = page.search_for(short_key)
                    for rect in instances:
                        results.append((page_num, rect))
    
    return results


def annotate_pdf(
    pdf_bytes: bytes,
    analysis_result: AnalysisResult,
    detected: Optional[DetectedDisclaimer] = None
) -> bytes:
    """
    Annotate PDF with issues found in analysis.
    
    Args:
        pdf_bytes: Original PDF file bytes
        analysis_result: Analysis result with issues
        detected: Detected disclaimer information
        
    Returns:
        Annotated PDF as bytes
    """
    # Open PDF
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    annotations_added = []
    
    # 1. Annotate missing required phrases
    if analysis_result.missing_required_phrases:
        for missing_phrase in analysis_result.missing_required_phrases:
            # Try to find where this phrase should be (in disclaimer section)
            if detected:
                # Search for disclaimer section
                disclaimer_locations = find_disclaimer_section(pdf_doc, detected.text)
                
                if disclaimer_locations:
                    # Add note near disclaimer section about missing phrase
                    page_num, rect = disclaimer_locations[0]
                    page = pdf_doc[page_num]
                    
                    add_highlight_with_popup(
                        page, rect,
                        f"Missing required phrase: {missing_phrase.phrase}",
                        (1.0, 0.8, 0.0),
                        opacity=0.5,
                        popup_title="Missing required",
                    )
                    annotations_added.append(f"Page {page_num + 1}: Missing phrase '{missing_phrase.phrase}'")
                else:
                    # Add note at end of document if disclaimer section not found
                    last_page = pdf_doc[-1]
                    rect = fitz.Rect(50, last_page.rect.height - 100, 200, last_page.rect.height - 50)
                    note = last_page.add_text_annot(rect.tl, f"Missing required phrase: {missing_phrase.phrase}")
                    note.set_info(content=f"Required phrase not found: {missing_phrase.phrase}")
                    note.update()
                    annotations_added.append(f"Last page: Missing phrase '{missing_phrase.phrase}'")
    
    # 2. Annotate disclaimer section based on risk level
    if detected and analysis_result.detected_disclaimer:
        disclaimer_locations = find_disclaimer_section(pdf_doc, detected.text)
        
        if disclaimer_locations:
            page_num, rect = disclaimer_locations[0]
            page = pdf_doc[page_num]
            
            # Color code based on risk level
            if analysis_result.risk_level.value == "HIGH":
                color = (1.0, 0.0, 0.0)  # Red
                message = f"HIGH RISK: {analysis_result.explanation[:100]}"
            elif analysis_result.risk_level.value == "MEDIUM":
                color = (1.0, 0.65, 0.0)  # Orange
                message = f"MEDIUM RISK: {analysis_result.explanation[:100]}"
            else:
                color = (0.0, 0.8, 0.0)  # Green
                message = f"LOW RISK: {analysis_result.explanation[:100]}"
            
            add_highlight_with_popup(
                page, rect, analysis_result.explanation[:200], color, opacity=0.5,
                popup_title=f"Risk: {analysis_result.risk_level.value}",
            )
            annotations_added.append(f"Page {page_num + 1}: Disclaimer section marked ({analysis_result.risk_level.value} risk)")
    
    # 3. If no disclaimer found, add warning annotation
    if not detected or not analysis_result.detected_disclaimer:
        # Add warning on first page
        first_page = pdf_doc[0]
        warning_rect = fitz.Rect(50, 50, 400, 100)
        note = first_page.add_text_annot(warning_rect.tl, "⚠ NO DISCLAIMER DETECTED")
        note.set_info(content="No disclaimer section was found in this document. Please add appropriate disclaimers.")
        note.set_colors(stroke=(1.0, 0.0, 0.0))  # Red
        note.update()
        annotations_added.append("Page 1: No disclaimer detected warning")
    
    # 4. Add summary annotation on first page
    first_page = pdf_doc[0]
    summary_y = first_page.rect.height - 150
    summary_rect = fitz.Rect(50, summary_y, 500, summary_y + 80)
    
    summary_text = f"""
    ANALYSIS SUMMARY:
    Status: {'✓ APPROVED' if analysis_result.is_approved else '✗ NOT APPROVED'}
    Risk Level: {analysis_result.risk_level.value}
    Missing Phrases: {len(analysis_result.missing_required_phrases)}
    """
    
    note = first_page.add_text_annot(summary_rect.tl, summary_text.strip())
    note.set_info(content=analysis_result.explanation)
    note.set_colors(stroke=(0.0, 0.0, 1.0))  # Blue
    note.update()
    
    # Save annotated PDF to bytes
    pdf_bytes_output = io.BytesIO()
    pdf_doc.save(pdf_bytes_output)
    pdf_doc.close()
    
    return pdf_bytes_output.getvalue()


def process_pdf_page_by_page(
    pdf_bytes: bytes,
    analysis_result: AnalysisResult,
    all_detected: List[DetectedDisclaimer] = None
) -> Tuple[bytes, List[dict]]:
    """
    Process PDF page by page and annotate with highlights and collect comments.
    Returns annotated PDF and list of comments for side panel.
    
    Args:
        pdf_bytes: Original PDF file bytes
        analysis_result: Analysis result with issues
        all_detected: List of all detected disclaimers
        
    Returns:
        Tuple of (annotated PDF bytes, list of comments)
    """
    if all_detected is None:
        all_detected = []
    
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    comments = []  # Collect all comments for side panel
    
    # Color palette - different colors for each comment
    color_palette = [
        (1.0, 0.2, 0.2),    # Red
        (1.0, 0.6, 0.0),    # Orange
        (1.0, 0.8, 0.0),    # Yellow
        (0.2, 0.8, 0.2),    # Green
        (0.2, 0.6, 1.0),    # Blue
        (0.6, 0.2, 1.0),    # Purple
        (1.0, 0.4, 0.8),    # Pink
        (0.0, 0.8, 0.8),    # Cyan
        (0.8, 0.6, 0.2),    # Brown
        (0.4, 0.4, 0.4),    # Gray
    ]
    color_index = 0
    
    # Check if there are any actual violations or footnote/formatting issues to annotate
    footnote_issues = getattr(analysis_result, "footnote_issues", None) or []
    formatting_issues = getattr(analysis_result, "formatting_issues", None) or []
    has_issues = (
        len(analysis_result.missing_required_phrases) > 0 or
        any(
            len(getattr(cr, 'violation_details', []) or []) > 0 or len(cr.violations) > 0 or len(cr.missing_required) > 0
            for cr in analysis_result.checklist_results
        ) or
        len(footnote_issues) > 0 or
        len(formatting_issues) > 0
    )
    
    # Only annotate if there are actual issues
    if not has_issues:
        # No issues - just save the PDF without annotations
        pdf_bytes_output = io.BytesIO()
        pdf_doc.save(pdf_bytes_output)
        pdf_doc.close()
        return pdf_bytes_output.getvalue(), []
    
    # First, process document-wide checklist results (jurisdiction=None)
    for cr in analysis_result.checklist_results:
        if cr.jurisdiction is None:  # Document-wide results
            issues_to_annotate = []
            # Use violation_details (with exact_text for highlighting) when present
            vd = getattr(cr, 'violation_details', None) or []
            for v in vd:
                issues_to_annotate.append({
                    "type": "violation",
                    "text": v.violation,
                    "reason": v.violation,
                    "exact_text": getattr(v, 'exact_text', None),
                    "severity": "HIGH",
                    "priority": 1,
                })
            if not vd:
                for violation in cr.violations:
                    issues_to_annotate.append({
                        "type": "violation",
                        "text": violation,
                        "reason": violation,
                        "exact_text": None,
                        "severity": "HIGH",
                        "priority": 1,
                    })
            for missing in cr.missing_required:
                issues_to_annotate.append({
                    "type": "missing_required",
                    "text": missing.phrase,
                    "reason": missing.reason or missing.phrase,
                    "exact_text": getattr(missing, 'exact_highlight_text', None),
                    "severity": "HIGH",
                    "priority": 2,
                })
            issues_to_annotate.sort(key=lambda x: x.get("priority", 99))

            for issue in issues_to_annotate:
                highlight_color = color_palette[color_index % len(color_palette)]
                color_index += 1
                color_hex = f"#{int(highlight_color[0]*255):02x}{int(highlight_color[1]*255):02x}{int(highlight_color[2]*255):02x}"
                found_highlight = False
                highlighted_text = ""
                search_text_for_nav = None
                all_instances = []

                exact = issue.get("exact_text") or issue.get("exact_highlight_text")
                keyword_fallback = None
                if issue["type"] == "violation" and not exact:
                    import re
                    violation_text = issue["text"]
                    if "%" in violation_text:
                        matches = re.findall(r'\d+%', violation_text)
                        if matches:
                            keyword_fallback = matches[0]
                    for word in ["guaranteed", "guarantee", "promise", "100%", "gain", "return", "profit"]:
                        if word.lower() in violation_text.lower():
                            keyword_fallback = keyword_fallback or word
                            break
                if issue["type"] == "missing_required" and not exact:
                    missing_text = issue["text"].lower()
                    if "risk" in missing_text:
                        keyword_fallback = "risk"
                    elif "past performance" in missing_text or "performance" in missing_text:
                        keyword_fallback = "past performance"
                    elif "investor" in missing_text:
                        keyword_fallback = "investor"

                all_instances = find_text_in_pdf_with_fallbacks(pdf_doc, exact, keyword_fallback)
                if not all_instances and keyword_fallback:
                    all_instances = find_text_in_pdf(pdf_doc, keyword_fallback)
                if all_instances:
                    # Create one comment per instance (per page occurrence)
                    for page_num_inst, highlight_rect in all_instances:
                        page = pdf_doc[page_num_inst]
                        add_highlight_with_popup(
                            page, highlight_rect, issue["reason"][:200],
                            highlight_color, opacity=0.5,
                            popup_title=issue.get("type", "Issue")[:40],
                        )
                        
                        # Get highlighted text for this specific instance
                        try:
                            instance_highlighted_text = page.get_textbox(highlight_rect)[:200]
                        except Exception:
                            instance_highlighted_text = (exact or keyword_fallback or issue["text"])[:200]
                        
                        search_text_for_nav = (exact or keyword_fallback or "")[:100]
                        comment = {
                            "page": page_num_inst + 1,
                            "text": issue["reason"][:300],
                            "type": issue["type"].replace("_", " ").title(),
                            "color": color_hex,
                            "highlighted_text": instance_highlighted_text or (issue["text"][:200] if issue.get("text") else ""),
                            "jurisdiction": "Document-wide",
                            "search_text": search_text_for_nav[:100] if search_text_for_nav else None,
                        }
                        comments.append(comment)
                    found_highlight = True
                elif issue["type"] in ("violation", "missing_required") and issue.get("severity") == "HIGH":
                    # No instances found but still add comment (e.g. missing required)
                    comment = {
                        "page": 1,
                        "text": issue["reason"][:300],
                        "type": issue["type"].replace("_", " ").title(),
                        "color": color_hex,
                        "highlighted_text": issue["text"][:200] if issue.get("text") else "",
                        "jurisdiction": "Document-wide",
                        "search_text": None,
                    }
                    comments.append(comment)
    
    # Process each detected disclaimer and annotate issues
    for detected in all_detected:
        if not detected or not detected.text:
            continue
        
        # Find disclaimer text in PDF
        disclaimer_locations = find_disclaimer_section(pdf_doc, detected.text)
        
        # Also try to find by searching for key sentences
        if not disclaimer_locations:
            # Search for first few sentences of the disclaimer
            sentences = detected.text.split('.')[:2]
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) > 30:
                    locations = find_text_in_pdf(pdf_doc, sentence[:100])
                    if locations:
                        disclaimer_locations.extend(locations)
                        break
        
        # Get checklist result for this jurisdiction
        checklist_result = None
        for cr in analysis_result.checklist_results:
            # Match by jurisdiction (handle both enum and string)
            if detected.jurisdiction:
                if (isinstance(cr.jurisdiction, str) and cr.jurisdiction == detected.jurisdiction.value) or \
                   (hasattr(cr.jurisdiction, 'value') and cr.jurisdiction == detected.jurisdiction):
                    checklist_result = cr
                    break
            elif cr.jurisdiction is None:
                # General disclaimer
                checklist_result = cr
                break
        
        # Only annotate if there are issues for this specific disclaimer
        has_jurisdiction_issues = (
            checklist_result and (
                len(checklist_result.missing_required) > 0 or
                len(checklist_result.violations) > 0
            )
        )
        
        if not has_jurisdiction_issues:
            continue  # Skip this disclaimer - no issues to annotate
        
        # Annotate issues for this disclaimer: one comment per unique issue (not per page)
        jur_name = detected.jurisdiction.value if detected.jurisdiction else "General"
        issues_to_annotate = []
        vd = getattr(checklist_result, 'violation_details', None) or []
        for v in vd:
            issues_to_annotate.append({
                "type": "violation",
                "text": v.violation,
                "reason": v.violation,
                "exact_text": getattr(v, 'exact_text', None),
                "severity": "HIGH",
                "priority": 1,
            })
        if not vd:
            for violation in checklist_result.violations:
                issues_to_annotate.append({
                    "type": "violation",
                    "text": violation,
                    "reason": violation,
                    "exact_text": None,
                    "severity": "HIGH",
                    "priority": 1,
                })
        for missing in checklist_result.missing_required:
            issues_to_annotate.append({
                "type": "missing_required",
                "text": missing.phrase,
                "reason": missing.reason or missing.phrase,
                "exact_text": getattr(missing, 'exact_highlight_text', None),
                "severity": "HIGH",
                "priority": 2,
            })
        issues_to_annotate.sort(key=lambda x: x.get("priority", 99))

        for issue in issues_to_annotate:
            highlight_color = color_palette[color_index % len(color_palette)]
            color_index += 1
            color_hex = f"#{int(highlight_color[0]*255):02x}{int(highlight_color[1]*255):02x}{int(highlight_color[2]*255):02x}"
            found_highlight = False
            highlighted_text = ""
            search_text_for_nav = None

            exact = issue.get("exact_text")
            keyword_fallback = None
            if not exact and issue["type"] == "violation":
                violation_words = [w for w in issue["text"].split() if len(w) > 4][:5]
                if violation_words:
                    for sentence in detected.text.split('.'):
                        if any(w.lower() in sentence.lower() for w in violation_words):
                            keyword_fallback = sentence.strip()[:80]
                            break
            if not exact and issue["type"] == "missing_required" and len(issue.get("text", "")) > 10:
                keyword_fallback = issue["text"][:80]

            # Search whole document; create one comment per instance (per page occurrence)
            all_instances = find_text_in_pdf_with_fallbacks(pdf_doc, exact, keyword_fallback)
            if not all_instances and keyword_fallback:
                all_instances = find_text_in_pdf(pdf_doc, keyword_fallback)
            if all_instances:
                for _pn, highlight_rect in all_instances:
                    p = pdf_doc[_pn]
                    add_highlight_with_popup(
                        p, highlight_rect, issue["reason"][:200],
                        highlight_color, opacity=0.5,
                        popup_title=issue.get("type", "Issue")[:40],
                    )
                    
                    # Get highlighted text for this specific instance
                    try:
                        instance_highlighted_text = p.get_textbox(highlight_rect)[:200]
                    except Exception:
                        instance_highlighted_text = (exact or keyword_fallback or issue["text"])[:200]
                    
                    search_text_for_nav = (exact or keyword_fallback or "")[:100]
                    comment_page = _pn + 1
                    comments.append({
                        "page": comment_page,
                        "text": issue["reason"][:300],
                        "type": issue["type"].replace("_", " ").title(),
                        "color": color_hex,
                        "highlighted_text": instance_highlighted_text or (issue.get("text", "")[:200]),
                        "jurisdiction": jur_name,
                        "search_text": search_text_for_nav[:100] if search_text_for_nav else None,
                    })
                found_highlight = True
            elif issue["type"] in ("violation", "missing_required") and issue.get("severity") == "HIGH":
                # No instances found but still add comment (e.g. missing required)
                comment_page = disclaimer_locations[0][0] + 1 if disclaimer_locations else 1
                comments.append({
                    "page": comment_page,
                    "text": issue["reason"][:300],
                    "type": issue["type"].replace("_", " ").title(),
                    "color": color_hex,
                    "highlighted_text": issue.get("text", "")[:200],
                    "jurisdiction": jur_name,
                    "search_text": None,
                })

    # Red text (edit remnants): add highlight on PDF and comment
    for fi in formatting_issues:
        if getattr(fi, "issue_type", None) == "unusual_color" and getattr(fi, "bbox", None) and len(fi.bbox) >= 4:
            try:
                page_idx = fi.page - 1
                if 0 <= page_idx < len(pdf_doc):
                    page = pdf_doc[page_idx]
                    r = fitz.Rect(fi.bbox[0], fi.bbox[1], fi.bbox[2], fi.bbox[3])
                    if r.get_area() > 0:
                        add_highlight_with_popup(
                            page, r, "Red text – possible edit remnant. Please remove or revise before finalising.",
                            (1.0, 0.3, 0.3), opacity=0.4, popup_title="Red text (edit remnant)",
                        )
            except Exception:
                pass

    # Footnote issues: add one comment per issue and highlight the problematic ref or footnote text in the PDF
    footnotes_locations = getattr(analysis_result, "footnotes_locations", None) or {}
    for fi in footnote_issues:
        color_hex = "#6f42c1"  # Purple for footnote
        comments.append({
            "page": fi.page,
            "text": fi.message,
            "type": "Footnote",
            "color": color_hex,
            "highlighted_text": (fi.reference or "")[:100],
            "jurisdiction": None,
            "search_text": fi.reference,
        })
        # Highlight in PDF: either the body reference (fi.bbox) or the footnote definition (footnotes_locations[ref])
        try:
            rect = None
            page_idx = fi.page - 1
            if getattr(fi, "bbox", None) and len(fi.bbox) >= 4:
                rect = fitz.Rect(fi.bbox[0], fi.bbox[1], fi.bbox[2], fi.bbox[3])
            elif fi.reference and footnotes_locations:
                loc = footnotes_locations.get(fi.reference)
                if loc and isinstance(loc.get("bbox"), (list, tuple)) and len(loc["bbox"]) >= 4:
                    page_idx = int(loc.get("page", fi.page)) - 1
                    rect = fitz.Rect(loc["bbox"][0], loc["bbox"][1], loc["bbox"][2], loc["bbox"][3])
            if rect is not None and 0 <= page_idx < len(pdf_doc):
                page = pdf_doc[page_idx]
                add_highlight_with_popup(
                    page, rect, (fi.message or "")[:200],
                    (0.43, 0.26, 0.76), opacity=0.4, popup_title="Footnote",
                )
        except Exception:
            pass
    # Formatting issues: red text (edit remnant) or existing highlight
    for fi in formatting_issues:
        color_hex = "#0dcaf0" if fi.issue_type == "existing_highlight" else "#dc3545"  # Cyan / red
        comments.append({
            "page": fi.page,
            "text": fi.message + (f" ({fi.color_hex})" if fi.color_hex else ""),
            "type": "Red text" if fi.issue_type == "unusual_color" else "Existing highlight",
            "color": color_hex,
            "highlighted_text": (fi.text or "")[:200],
            "jurisdiction": None,
            "search_text": fi.text[:100] if fi.text else None,
        })

    # Deduplicate comments: same violation on same page = one comment (keep unique page occurrences)
    seen_comment_key: set = set()
    comments_deduped = []
    for c in comments:
        # Key includes page number so same violation on different pages = different comments
        key = (
            (c.get("text") or "")[:80],
            (c.get("highlighted_text") or c.get("search_text") or "")[:60],
            c.get("page", 0),
        )
        if key in seen_comment_key:
            continue
        seen_comment_key.add(key)
        comments_deduped.append(c)
    comments = comments_deduped

    # Sort comments by page number
    comments.sort(key=lambda c: c.get("page", 0))

    # Save annotated PDF - use incremental save to preserve annotations
    pdf_bytes_output = io.BytesIO()
    try:
        pdf_doc.save(pdf_bytes_output, incremental=True)
    except Exception:
        pdf_doc.save(pdf_bytes_output)
    pdf_doc.close()

    return pdf_bytes_output.getvalue(), comments
