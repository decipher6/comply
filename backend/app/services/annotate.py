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
                    
                    # Add highlight with note
                    highlight = page.add_highlight_annot(rect)
                    highlight.set_info(content=f"Missing required phrase: {missing_phrase.phrase}")
                    highlight.update()
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
            
            # Add highlight
            highlight = page.add_highlight_annot(rect)
            highlight.set_colors(stroke=color)
            highlight.set_info(content=message)
            highlight.update()
            
            # Add text annotation with detailed info
            note_rect = fitz.Rect(rect.x0, rect.y0 - 30, rect.x1, rect.y0)
            note = page.add_text_annot(note_rect.tl, f"Risk: {analysis_result.risk_level.value}")
            note.set_info(content=analysis_result.explanation[:200])
            note.update()
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
    
    # Check if there are any actual issues to annotate
    has_issues = (
        len(analysis_result.missing_required_phrases) > 0 or
        any(len(cr.violations) > 0 or len(cr.missing_required) > 0 
            for cr in analysis_result.checklist_results) or
        any(not item.is_compliant and item.missing_details 
            for cr in analysis_result.checklist_results 
            for item in cr.checklist_items)
    )
    
    # Only annotate if there are actual issues
    if not has_issues:
        # No issues - just save the PDF without annotations
        pdf_bytes_output = io.BytesIO()
        pdf_doc.save(pdf_bytes_output)
        pdf_doc.close()
        return pdf_bytes_output.getvalue(), []
    
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
            if cr.jurisdiction == detected.jurisdiction:
                checklist_result = cr
                break
        
        # Only annotate if there are issues for this specific disclaimer
        has_jurisdiction_issues = (
            checklist_result and (
                len(checklist_result.missing_required) > 0 or
                len(checklist_result.violations) > 0 or
                any(not item.is_compliant and item.missing_details 
                    for item in checklist_result.checklist_items)
            )
        )
        
        if not has_jurisdiction_issues:
            continue  # Skip this disclaimer - no issues to annotate
        
        # Annotate issues for this disclaimer
        if disclaimer_locations:
            # Group by page
            pages_with_disclaimers = {}
            for page_num, rect in disclaimer_locations:
                if page_num not in pages_with_disclaimers:
                    pages_with_disclaimers[page_num] = []
                pages_with_disclaimers[page_num].append(rect)
            
            for page_num, rects in pages_with_disclaimers.items():
                if page_num < len(pdf_doc):
                    page = pdf_doc[page_num]
                    page_rect = page.rect
                    page_width = page_rect.width
                    page_height = page_rect.height
                    
                    jur_name = detected.jurisdiction.value if detected.jurisdiction else "General"
                    
                    # Find text blocks for this disclaimer
                    text_blocks = page.get_text("blocks")
                    disclaimer_blocks = []
                    
                    detected_lower = detected.text.lower()
                    key_words = [w for w in detected_lower.split() if len(w) > 4][:10]
                    
                    for block in text_blocks:
                        if len(block) > 4:
                            block_text = block[4].lower()
                            matches = sum(1 for word in key_words if word in block_text)
                            if matches >= 2:
                                disclaimer_blocks.append(block)
                    
                    # Get first rect for positioning
                    first_rect = rects[0] if rects else None
                    if not first_rect and disclaimer_blocks:
                        first_block = disclaimer_blocks[0]
                        first_rect = fitz.Rect(first_block[0], first_block[1], first_block[2], first_block[3])
                    
                    if not first_rect:
                        continue
                    
                    # Collect all issues for this jurisdiction
                    issues_to_annotate = []
                    
                    # Add missing required phrases from checklist result
                    for missing in checklist_result.missing_required:
                        issues_to_annotate.append({
                            "type": "missing_required",
                            "text": missing.phrase,
                            "reason": missing.reason,
                            "severity": "HIGH"
                        })
                    
                    # Add violations
                    for violation in checklist_result.violations:
                        issues_to_annotate.append({
                            "type": "violation",
                            "text": violation,
                            "reason": "Checklist violation",
                            "severity": "HIGH"
                        })
                    
                    # Add non-compliant items with details
                    for item in checklist_result.checklist_items:
                        if not item.is_compliant and item.missing_details:
                            issues_to_annotate.append({
                                "type": "non_compliant",
                                "text": item.item,
                                "reason": item.missing_details,
                                "severity": "HIGH" if item.is_required else "MEDIUM"
                            })
                    
                    # Also check analysis_result.missing_required_phrases for this jurisdiction
                    for missing_phrase in analysis_result.missing_required_phrases:
                        # Check if this missing phrase is relevant to this jurisdiction
                        # (simple check - if jurisdiction matches or is general)
                        if (detected.jurisdiction is None and missing_phrase.phrase) or \
                           (detected.jurisdiction and any(
                               detected.jurisdiction.value.lower() in missing_phrase.phrase.lower() or
                               missing_phrase.phrase.lower() in detected.text.lower()
                               for _ in [1])):
                            # Avoid duplicates
                            if not any(issue["text"] == missing_phrase.phrase for issue in issues_to_annotate):
                                issues_to_annotate.append({
                                    "type": "missing_required",
                                    "text": missing_phrase.phrase,
                                    "reason": missing_phrase.reason or "Required phrase not found",
                                    "severity": "HIGH"
                                })
                    
                    # Annotate each issue with highlights and collect comments
                    for issue in issues_to_annotate:
                        # Get unique color for this comment
                        highlight_color = color_palette[color_index % len(color_palette)]
                        color_index += 1
                        
                        # Convert RGB to hex for frontend
                        color_hex = f"#{int(highlight_color[0]*255):02x}{int(highlight_color[1]*255):02x}{int(highlight_color[2]*255):02x}"
                        
                        # Try to find and highlight the problematic text
                        found_highlight = False
                        highlighted_text = ""
                        
                        if issue["text"] and len(issue["text"]) > 10 and issue["text"].upper() != "MISSING":
                            # Search for the issue text across all pages using improved search
                            search_text = issue["text"].strip()
                            all_instances = find_text_in_pdf(pdf_doc, search_text)
                            
                            # Filter instances for current page
                            instances = [inst[1] for inst in all_instances if inst[0] == page_num]
                            
                            if instances:
                                # Highlight ALL occurrences of this text on the page
                                for highlight_rect in instances:
                                    highlight = page.add_highlight_annot(highlight_rect)
                                    # Set color - highlights use fill, not stroke
                                    highlight.set_colors(stroke=highlight_color)
                                    highlight.set_info(content=issue["reason"])
                                    highlight.set_opacity(0.4)
                                    highlight.update()
                                
                                # Use the first instance for getting text
                                highlight_rect = instances[0]
                                
                                # Get the highlighted text
                                try:
                                    highlighted_text = page.get_textbox(highlight_rect)[:200]
                                except:
                                    highlighted_text = issue["text"][:200]
                                
                                found_highlight = True
                        
                        # Add comment to list
                        comment = {
                            "page": page_num + 1,
                            "text": issue["reason"],
                            "type": issue["type"].replace("_", " ").title(),
                            "color": color_hex,
                            "highlighted_text": highlighted_text or (issue["text"][:200] if issue["text"] else ""),
                            "jurisdiction": jur_name
                        }
                        comments.append(comment)
                    
                    # No LLM suggestions - only use checklist results
    
    # No summary annotation - status is shown in frontend only
    
    # Save annotated PDF - use incremental save to preserve annotations
    pdf_bytes_output = io.BytesIO()
    try:
        pdf_doc.save(pdf_bytes_output, incremental=True)
    except:
        # Fallback to regular save if incremental fails
        pdf_doc.save(pdf_bytes_output)
    pdf_doc.close()
    
    return pdf_bytes_output.getvalue(), comments
