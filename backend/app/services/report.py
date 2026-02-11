# app/services/report.py
# Note: google.generativeai is deprecated in favor of google.genai
# Keeping current package for now as it still works. Consider migrating to google.genai in future.
import google.generativeai as genai
import json
from app.config import settings
from app.models import (
    AnalysisResult, DetectedDisclaimer, ComparisonResult,
    MissingPhrase, RiskLevel, ChecklistItem, ChecklistResult, ViolationDetail,
    FootnoteIssue, FormattingIssue,
)
from app.services.rules import classify_risk_level, determine_approval_status
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed


def initialize_gemini():
    """Initialize Gemini API client."""
    if settings.GEMINI_API_KEY:
        genai.configure(api_key=settings.GEMINI_API_KEY)


def get_llm_suggestions(
    detected: Optional[DetectedDisclaimer],
    comparison_results: List[ComparisonResult],
    missing_phrases: List[MissingPhrase]
) -> Optional[str]:
    """
    Get LLM suggestions for risky wording or missing elements.
    
    Args:
        detected: Detected disclaimer
        comparison_results: Comparison results
        missing_phrases: Missing required phrases
        
    Returns:
        LLM suggestions as string or None
    """
    if not settings.GEMINI_API_KEY or not detected:
        return None
    
    try:
        initialize_gemini()
        # Use deterministic generation config for legal compliance
        generation_config = {
            "temperature": 0.0,  # Completely deterministic
            "top_p": 0.95,
            "top_k": 40,
        }
        model = genai.GenerativeModel('gemini-3-flash-preview', generation_config=generation_config)
        
        best_match_info = ""
        if comparison_results:
            best = comparison_results[0]
            best_match_info = f"Best match similarity: {best.similarity_score:.2f}\n"
            best_match_info += f"Matched phrases: {', '.join(best.matched_phrases[:5])}\n"
            best_match_info += f"Missing phrases: {', '.join(best.missing_phrases[:5])}"
        
        # Get compliance checklist for context
        from app.services.compliance_checklist import get_checklist_for_jurisdiction
        jurisdiction_name = detected.jurisdiction.value if detected.jurisdiction else None
        checklist = get_checklist_for_jurisdiction(jurisdiction_name)
        
        prompt = f"""You are a compliance analyst reviewing a marketing disclaimer. ONLY flag issues that violate the compliance checklist below.

COMPLIANCE CHECKLIST:
{checklist}

Detected Disclaimer:
{detected.text[:2000]}

Jurisdiction: {jurisdiction_name or 'Unknown'}

CRITICAL RULES:
1. ONLY flag issues that are explicitly mentioned in the compliance checklist above
2. Do NOT make up issues or flag things not in the checklist
3. Do NOT flag minor wording variations - only actual violations
4. For each issue, provide the EXACT text from the disclaimer that violates the checklist

Format your response as:
ISSUE 1:
TEXT: [EXACT text excerpt from disclaimer that violates checklist - minimum 20 characters]
SUGGESTION: [reference the specific checklist requirement violated]
TYPE: [missing_required_statement, false_misleading_statement, missing_risk_warning]

ISSUE 2:
TEXT: [EXACT text excerpt]
SUGGESTION: [reference checklist requirement]
TYPE: [issue type]

If the disclaimer meets all checklist requirements, respond: "No issues found. Disclaimer is compliant with the checklist."

ONLY flag checklist violations - nothing else."""
        
        # Use deterministic generation with temperature=0
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40}
        )
        return response.text
        
    except Exception as e:
        return f"Could not generate LLM suggestions: {str(e)}"


def generate_explanation(
    is_approved: bool,
    risk_level: RiskLevel,
    best_match: Optional[ComparisonResult],
    missing_phrases: List[MissingPhrase],
    detected: Optional[DetectedDisclaimer],
    checklist_violations: List[str] = None
) -> str:
    """
    Generate a human-readable explanation of the analysis result based on checklist compliance.
    
    Args:
        is_approved: Whether disclaimer is approved
        risk_level: Risk level classification
        best_match: Best matching approved disclaimer (for reference)
        missing_phrases: Missing required phrases from checklist
        detected: Detected disclaimer
        checklist_violations: List of checklist violations
        
    Returns:
        Explanation string
    """
    if checklist_violations is None:
        checklist_violations = []
    
    explanation_parts = []
    
    if is_approved:
        explanation_parts.append("✓ Disclaimer is APPROVED - All checklist requirements met.")
    else:
        explanation_parts.append("✗ Disclaimer is NOT APPROVED - Checklist requirements not fully met.")
    
    explanation_parts.append(f"Risk Level: {risk_level.value} (based on checklist compliance)")
    
    if missing_phrases:
        explanation_parts.append(
            f"\nMissing {len(missing_phrases)} required element(s) from checklist:"
        )
        for phrase in missing_phrases[:5]:  # Show first 5
            explanation_parts.append(f"  - {phrase.phrase}")
            if phrase.reason:
                explanation_parts.append(f"    Reason: {phrase.reason}")
        if len(missing_phrases) > 5:
            explanation_parts.append(f"  ... and {len(missing_phrases) - 5} more")
    
    if checklist_violations:
        explanation_parts.append(f"\nChecklist Violations ({len(checklist_violations)}):")
        for violation in checklist_violations[:5]:
            explanation_parts.append(f"  - {violation}")
        if len(checklist_violations) > 5:
            explanation_parts.append(f"  ... and {len(checklist_violations) - 5} more")
    
    if not detected:
        explanation_parts.append("\n⚠ No disclaimer was detected in the document.")
    
    return "\n".join(explanation_parts)


def generate_summary_blurb(
    is_approved: bool,
    missing_phrases: List[MissingPhrase],
    checklist_violations: List[str],
    detected: Optional[DetectedDisclaimer],
) -> str:
    """Short 1–2 sentence summary for the result UI (why OK / why not)."""
    if is_approved:
        return "This document meets the checklist requirements. No violations or missing required statements were found."
    parts = []
    if checklist_violations:
        n = len(checklist_violations)
        parts.append(f"{n} violation{'s' if n != 1 else ''} found (e.g. {checklist_violations[0][:60]}{'...' if len(checklist_violations[0]) > 60 else ''}).")
    if missing_phrases:
        n = len(missing_phrases)
        parts.append(f"{n} required element{'s' if n != 1 else ''} missing from the materials.")
    if not detected:
        parts.append("No disclaimer was detected.")
    if not parts:
        return "This document was not approved. Review the checklist for details."
    return " ".join(parts)


def check_checklist_compliance_with_items(
    detected: Optional[DetectedDisclaimer],
    jurisdiction: Optional[str] = None
) -> tuple[List[MissingPhrase], List[str], List]:
    """
    Check disclaimer compliance against checklist using LLM and return detailed checklist items.
    
    Args:
        detected: Detected disclaimer
        jurisdiction: Optional jurisdiction
        
    Returns:
        Tuple of (missing_phrases, checklist_violations, checklist_items_status)
    """
    from app.services.compliance_checklist import get_checklist_for_jurisdiction, parse_checklist_items
    
    if not settings.GEMINI_API_KEY or not detected:
        # Return empty results with all items marked as non-compliant
        checklist_text = get_checklist_for_jurisdiction(jurisdiction)
        items = parse_checklist_items(checklist_text)
        checklist_items = [
            ChecklistItem(
                item=item_text,
                section=section,
                is_required=is_required,
                is_compliant=False,
                missing_details="Disclaimer not analyzed"
            )
            for item_text, section, is_required in items
        ]
        return [], [], checklist_items
    
    try:
        initialize_gemini()
        # Use deterministic generation config for legal compliance
        generation_config = {
            "temperature": 0.0,  # Completely deterministic
            "top_p": 0.95,
            "top_k": 40,
        }
        model = genai.GenerativeModel('gemini-3-flash-preview', generation_config=generation_config)
        
        # Get compliance checklist
        jurisdiction_name = detected.jurisdiction.value if detected.jurisdiction else jurisdiction
        checklist = get_checklist_for_jurisdiction(jurisdiction_name)
        checklist_items_parsed = parse_checklist_items(checklist)
        
        # Include ALL checklist items (required and optional) for comprehensive checking
        all_items_list = "\n".join([f"{i+1}. {item_text} {'*REQUIRED*' if is_required else ''}" 
                                   for i, (item_text, _, is_required) in enumerate(checklist_items_parsed)])
        
        prompt = f"""You are a compliance analyst checking a disclaimer against a regulatory compliance checklist. This is a LEGAL matter - be DETERMINISTIC, THOROUGH, and CONSISTENT.

COMPLIANCE CHECKLIST ITEMS:
{all_items_list}

DETECTED DISCLAIMER TEXT:
{detected.text[:5000] if len(detected.text) > 5000 else detected.text}

JURISDICTION: {jurisdiction_name or 'General'}

DETERMINISTIC RULES (FOLLOW EXACTLY):
1. For EACH numbered checklist item above, check if it is present/compliant in the disclaimer text
2. Use EXACT text matching - search for the exact phrases/requirements mentioned in each item
3. For items marked *REQUIRED*, they MUST be present - mark non-compliant ONLY if clearly absent
4. For violations, check the ENTIRE disclaimer text for:
   - Promises of specific returns/gains (e.g., "100% gain", "guaranteed return", "promise of X%")
   - False or misleading statements
   - Forecasts of future prices without proper disclaimers
   - Unfair promises or guarantees
   - Missing risk warnings (if required)
   - Missing past performance disclaimers (if past performance is mentioned)
5. Be CONSERVATIVE - if unsure or wording is similar, mark as compliant
6. Do NOT interpret or infer - use literal matching only

VIOLATION EXAMPLES (flag these if found):
- "100% gain" or "guaranteed 100% return" → VIOLATION: promise of specific return
- "We promise X% return" → VIOLATION: promise without proper disclaimers
- "Guaranteed profit" → VIOLATION: misleading guarantee

Respond in this EXACT JSON format (no markdown, no extra text):
{{
  "checklist_items": [
    {{
      "item": "exact checklist item text",
      "section": "section name",
      "is_compliant": true/false,
      "missing_details": "details if not compliant, empty string if compliant"
    }}
  ],
  "missing_required": [
    {{
      "element": "description of missing required element",
      "checklist_reference": "which checklist item it violates"
    }}
  ],
  "violations": [
    {{
      "violation": "description of violation",
      "checklist_reference": "which checklist item it violates"
    }}
  ]
}}

If everything is compliant, return all items with "is_compliant": true and empty arrays for missing_required and violations.

CRITICAL: Be CONSISTENT - same input must produce same output. Use exact matching, not interpretation."""
        
        # Use deterministic generation with temperature=0 and output limit for speed
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.0,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 2048  # Limit response size
            }
        )
        response_text = response.text.strip()
        
        # Parse JSON
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"JSON parse error in check_checklist_compliance_with_items: {e}")
            print(f"Response: {response_text[:500]}")
            # Return compliant state on parse error to be conservative
            checklist_text = get_checklist_for_jurisdiction(jurisdiction_name if 'jurisdiction_name' in locals() else jurisdiction)
            items = parse_checklist_items(checklist_text)
            checklist_items = [
                ChecklistItem(
                    item=item_text,
                    section=section,
                    is_required=is_required,
                    is_compliant=True,  # Default to compliant on error
                    missing_details=""
                )
                for item_text, section, is_required in items
            ]
            return [], [], checklist_items
        
        # Build checklist items with status
        checklist_items_dict = {item["item"]: item for item in data.get("checklist_items", [])}
        checklist_items = []
        
        for item_text, section, is_required in checklist_items_parsed:
            item_data = checklist_items_dict.get(item_text, {})
            checklist_items.append(ChecklistItem(
                item=item_text,
                section=section,
                is_required=is_required,
                is_compliant=item_data.get("is_compliant", False),
                missing_details=item_data.get("missing_details", "")
            ))
        
        missing_phrases = [
            MissingPhrase(
                phrase=item["element"],
                required=True,
                reason=f"Required by checklist: {item.get('checklist_reference', 'N/A')}"
            )
            for item in data.get("missing_required", [])
        ]
        
        violations = [
            item["violation"] for item in data.get("violations", [])
        ]
        
        return missing_phrases, violations, checklist_items
        
    except Exception as e:
        print(f"Error checking checklist compliance: {e}")
        # Return items with all marked as non-compliant
        checklist_text = get_checklist_for_jurisdiction(jurisdiction_name if 'jurisdiction_name' in locals() else jurisdiction)
        items = parse_checklist_items(checklist_text)
        checklist_items = [
            ChecklistItem(
                item=item_text,
                section=section,
                is_required=is_required,
                is_compliant=False,
                missing_details=f"Error analyzing: {str(e)}"
            )
            for item_text, section, is_required in items
        ]
        return [], [], checklist_items


RED_FLAG_PHRASES = (
    "guaranteed return", "guaranteed profit", "guaranteed gain", "guaranteed growth", "guaranteed yield",
    "100% return", "100% gain", "promise of return", "promise of profit", "promise of gain",
    "future price of", "forecast.*price", "specific return", "we guarantee", "we promise",
    "false or misleading", "misleading statement", "not necessarily.*future",
)

def _check_single_checklist_item(
    detected: DetectedDisclaimer,
    item_text: str,
    section: str,
    is_required: bool,
    jurisdiction_name: Optional[str],
    is_prohibition: bool = False,
) -> dict:
    """
    One small LLM call: check a single checklist requirement against the disclaimer.
    Returns dict with is_compliant, missing_details, exact_highlight_text (exact quote from disclaimer to highlight).
    """
    if not settings.GEMINI_API_KEY or not detected or not item_text.strip():
        return {"is_compliant": True, "missing_details": "", "exact_highlight_text": ""}

    try:
        initialize_gemini()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        disclaimer_snippet = (detected.text[:2800] + "...") if len(detected.text) > 2800 else detected.text

        prohibition_instruction = ""
        if is_prohibition:
            prohibition_instruction = """
This is a PROHIBITION: the material MUST NOT contain what the requirement forbids.
If the disclaimer or document text contains ANY of these RED FLAGS you MUST set is_compliant: false and quote the exact phrase in exact_highlight_text:
- "guaranteed" (return, profit, gain, growth, yield)
- Specific percentage return or gain (e.g. "10% return", "100% gain", "X% growth")
- "promise" of return/profit/gain
- "future price" or forecasting the price of a security
- False or misleading statements
- Language that presents as opinion/recommendation of Greenstone
Search the disclaimer text for these; if found, quote the EXACT offending phrase (15-120 chars) in exact_highlight_text.
See checklist: Marketing Materials must not include false or misleading statements; must not forecast future price; statements must be fair and not misleading.
"""

        prompt = f"""You are a compliance analyst. Check this single requirement against the disclaimer. Be DETERMINISTIC. Flag violations; do not be overly conservative.

REQUIREMENT ({section}):
{item_text}
{"This is REQUIRED - the statement must be present." if is_required else "This is a requirement that must be satisfied." if not is_prohibition else "This is a PROHIBITION - the material must NOT contain what is forbidden."}
{prohibition_instruction}

DISCLAIMER TEXT:
{disclaimer_snippet}

JURISDICTION: {jurisdiction_name or "General"}

RULES:
1. If the requirement is clearly satisfied (and for prohibitions: no forbidden content appears), respond is_compliant: true.
2. If the requirement is NOT satisfied—missing required statement OR forbidden content is present—set is_compliant: false and provide:
   - missing_details: one short sentence (e.g. "Material contains guaranteed return claim" or "Missing past performance disclaimer")
   - exact_highlight_text: an EXACT quote from the disclaimer (15-150 characters) that violates or is wrong. Copy verbatim so we can locate it in the PDF. For missing items use a phrase near where it should appear.
3. For prohibitions: if you see guaranteed returns, % returns, promises of profit, future price forecasts, or misleading statements, you MUST set is_compliant: false and quote the exact phrase.

Respond in this EXACT JSON format only:
{{"is_compliant": true or false, "missing_details": "...", "exact_highlight_text": "..."}}
"""

        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40, "max_output_tokens": 350}
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        return {
            "is_compliant": bool(data.get("is_compliant", True)),
            "missing_details": str(data.get("missing_details", "")).strip() or "",
            "exact_highlight_text": str(data.get("exact_highlight_text", "")).strip()[:200] or "",
        }
    except Exception:
        return {"is_compliant": True, "missing_details": "", "exact_highlight_text": ""}


def check_all_disclaimers_compliance_multi_call(
    all_detected: List[DetectedDisclaimer],
    jurisdiction: Optional[str] = None,
    max_workers: int = 6,
) -> List[ChecklistResult]:
    """
    Check compliance for all disclaimers using multiple small LLM calls: one call per
    required and prohibition checklist item per disclaimer. Catches red flags (guaranteed
    returns, misleading statements, etc.) by checking "must not" / "should not" items.
    """
    from app.services.compliance_checklist import (
        get_checklist_for_jurisdiction,
        parse_checklist_items,
        is_prohibition_item,
    )

    if not settings.GEMINI_API_KEY or not all_detected:
        return []

    checklist_results = []
    for detected in all_detected:
        jur_name = detected.jurisdiction.value if detected.jurisdiction else jurisdiction or "General"
        checklist = get_checklist_for_jurisdiction(jur_name)
        items_parsed = parse_checklist_items(checklist)
        required_items = [(t, s, r) for t, s, r in items_parsed if r]
        prohibition_items = [(t, s, False) for t, s, r in items_parsed if not r and is_prohibition_item(t)]
        optional_other = [(t, s, r) for t, s, r in items_parsed if not r and not is_prohibition_item(t)]
        # Run single-item check for required + prohibition (so we catch "must not include false statements" etc.)
        work = required_items + prohibition_items

        checklist_items = []
        missing_required = []
        violation_details_list: List[ViolationDetail] = []

        def run_one(args: Tuple) -> Tuple[str, str, bool, bool, dict]:
            item_text, section, is_req = args
            is_prohib = is_prohibition_item(item_text)
            res = _check_single_checklist_item(detected, item_text, section, is_req, jur_name, is_prohibition=is_prohib)
            return (item_text, section, is_req, is_prohib, res)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(run_one, w): w for w in work}
            for future in as_completed(futures):
                try:
                    item_text, section, is_required, _is_prohib, res = future.result()
                    exact_highlight = res.get("exact_highlight_text", "") or ""
                    checklist_items.append(ChecklistItem(
                        item=item_text,
                        section=section,
                        is_required=is_required,
                        is_compliant=res.get("is_compliant", True),
                        missing_details=res.get("missing_details", "") or "",
                        exact_highlight_text=exact_highlight or None,
                    ))
                    if not res.get("is_compliant", True):
                        reason = res.get("missing_details", "") or "Not compliant"
                        missing_required.append(MissingPhrase(
                            phrase=item_text[:200],
                            required=is_required,
                            reason=reason,
                            exact_highlight_text=exact_highlight or None,
                        ))
                        violation_details_list.append(ViolationDetail(
                            violation=reason,
                            exact_text=exact_highlight or None,
                        ))
                except Exception:
                    pass

        # Other optional items (not required, not prohibition): mark compliant
        for item_text, section, is_required in optional_other:
            checklist_items.append(ChecklistItem(
                item=item_text,
                section=section,
                is_required=is_required,
                is_compliant=True,
                missing_details="",
                exact_highlight_text=None,
            ))

        violations = [v.violation for v in violation_details_list]

        checklist_results.append(ChecklistResult(
            jurisdiction=detected.jurisdiction,
            checklist_items=checklist_items,
            missing_required=missing_required,
            violations=violations,
            violation_details=violation_details_list,
        ))

    return checklist_results


def check_checklist_compliance(
    detected: Optional[DetectedDisclaimer],
    jurisdiction: Optional[str] = None
) -> tuple[List[MissingPhrase], List[str]]:
    """
    Check disclaimer compliance against checklist using LLM.
    Returns missing phrases and violations.
    
    Args:
        detected: Detected disclaimer
        jurisdiction: Optional jurisdiction
        
    Returns:
        Tuple of (missing_phrases, checklist_violations)
    """
    if not settings.GEMINI_API_KEY or not detected:
        return [], []
    
    try:
        initialize_gemini()
        # Model initialization - generation config passed to generate_content
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Get compliance checklist
        from app.services.compliance_checklist import get_checklist_for_jurisdiction
        jurisdiction_name = detected.jurisdiction.value if detected.jurisdiction else jurisdiction
        checklist = get_checklist_for_jurisdiction(jurisdiction_name)
        
        prompt = f"""You are a compliance analyst checking a disclaimer against a regulatory compliance checklist. This is a LEGAL matter - be DETERMINISTIC and CONSISTENT.

COMPLIANCE CHECKLIST:
{checklist}

DETECTED DISCLAIMER:
{detected.text}

JURISDICTION: {jurisdiction_name or 'General'}

DETERMINISTIC RULES (FOLLOW EXACTLY):
1. ONLY check against checklist items above - do NOT add requirements
2. For required items (marked with *), check if present - mark missing if absent
3. For violations, check the ENTIRE disclaimer text for:
   - Promises of specific returns/gains (e.g., "100% gain", "guaranteed return", "promise of X%")
   - False or misleading statements
   - Forecasts of future prices without proper disclaimers
   - Unfair promises or guarantees
4. Be CONSERVATIVE - if unsure, mark as compliant
5. Use EXACT matching - do not interpret

VIOLATION EXAMPLES (flag these if found):
- "100% gain" or "guaranteed 100% return" → VIOLATION: promise of specific return
- "We promise X% return" → VIOLATION: promise without proper disclaimers
- "Guaranteed profit" → VIOLATION: misleading guarantee

CRITICAL: ONLY flag violations that are explicitly mentioned in the checklist above. Do NOT add your own requirements.

CRITICAL RULES:
1. ONLY check against the checklist items above - do NOT add your own requirements
2. ONLY flag missing required elements (marked with * in checklist)
3. ONLY flag violations that are explicitly mentioned in the checklist (including promises of returns/gains)
4. Do NOT make up violations or requirements not in the checklist

Respond in this EXACT JSON format:
{{
  "missing_required": [
    {{
      "element": "description of missing required element",
      "checklist_reference": "which checklist item it violates"
    }}
  ],
  "violations": [
    {{
      "violation": "description of violation",
      "checklist_reference": "which checklist item it violates"
    }}
  ]
}}

If everything is compliant, return: {{"missing_required": [], "violations": []}}

CRITICAL: Be CONSISTENT - same input must produce same output. Use exact matching."""
        
        # Use deterministic generation with temperature=0
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40}
        )
        response_text = response.text.strip()
        
        # Parse JSON
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        data = json.loads(response_text)
        
        missing_phrases = [
            MissingPhrase(
                phrase=item["element"],
                required=True,
                reason=f"Required by checklist: {item.get('checklist_reference', 'N/A')}"
            )
            for item in data.get("missing_required", [])
        ]
        
        violations = [
            item["violation"] for item in data.get("violations", [])
        ]
        
        return missing_phrases, violations
        
    except Exception as e:
        print(f"Error checking checklist compliance: {e}")
        return [], []


def scan_document_red_flags(pdf_bytes: bytes) -> List[ViolationDetail]:
    """
    Fast scan of full document text for obvious red-flag phrases (guaranteed return,
    specific % return, promise of profit, etc.). Ensures we never miss obvious violations.
    """
    import re
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for i in range(len(doc)):
            full_text += doc[i].get_text() + "\n"
        doc.close()
    except Exception:
        return []

    full_text_lower = full_text.lower()
    details: List[ViolationDetail] = []
    seen_quotes: set = set()

    # Patterns that indicate checklist violations (must not include false/misleading, guaranteed, etc.)
    patterns = [
        (r"(?:guaranteed\s+(?:return|profit|gain|growth|yield|income)|guarantee\s+(?:of\s+)?\d+%?)", "Guaranteed return or profit claim"),
        (r"\d{1,3}%\s*(?:return|gain|growth|yield|profit)\b", "Specific percentage return/gain claim"),
        (r"promise(?:d|s)?\s+(?:of\s+)?(?:\d+%?\s+)?(?:return|profit|gain)", "Promise of return or profit"),
        (r"(?:forecast|predict(?:ing|ed)?)\s+(?:the\s+)?(?:future\s+)?(?:price|value)\s+of", "Forecast of future price"),
        (r"100%\s*(?:return|gain|growth|safe)", "100% return or similar claim"),
        (r"(?:we\s+)?(?:guarantee|promise)\s+", "We guarantee/promise statement"),
        (r"false\s+or\s+misleading|misleading\s+statement", "False or misleading statement reference"),
    ]

    for pattern, violation_desc in patterns:
        for m in re.finditer(pattern, full_text, re.IGNORECASE):
            exact = m.group(0).strip()[:150]
            if len(exact) < 10:
                continue
            key = exact.lower().strip()
            if key in seen_quotes:
                continue
            seen_quotes.add(key)
            details.append(ViolationDetail(violation=violation_desc, exact_text=exact))

    return details


def _normalize_exact_key(exact: Optional[str]) -> str:
    """Normalize for deduplication: same phrase in different case/whitespace = same key."""
    if not exact or not str(exact).strip():
        return ""
    return " ".join(str(exact).lower().strip().split())[:120]


def deduplicate_violation_details(vd_list: List[ViolationDetail]) -> List[ViolationDetail]:
    """Keep one ViolationDetail per distinct exact_text (or per violation if no exact_text)."""
    seen_keys: set = set()
    out: List[ViolationDetail] = []
    for v in vd_list:
        key = _normalize_exact_key(v.exact_text) if v.exact_text else ("desc:" + (v.violation or "")[:80])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(v)
    return out


def check_entire_document_compliance_chunked(
    pdf_bytes: bytes,
    jurisdictions_detected: List[str],
    jurisdiction: Optional[str] = None,
    pages_per_chunk: int = 12,
    max_workers: int = 3,
) -> List[ChecklistResult]:
    """
    Check the entire document in page chunks (text-only, no PDF upload). Smaller context per call, faster.
    Runs chunk checks in parallel.
    """
    from app.services.compliance_checklist import get_checklist_for_jurisdiction, parse_checklist_items
    import fitz

    if not settings.GEMINI_API_KEY:
        return []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        num_pages = len(doc)
        if num_pages == 0:
            doc.close()
            return []

        chunks = []
        for start in range(0, num_pages, pages_per_chunk):
            end = min(start + pages_per_chunk, num_pages)
            text_parts = []
            for p in range(start, end):
                text_parts.append(doc[p].get_text())
            chunk_text = "\n\n".join(text_parts)
            if len(chunk_text) > 18000:
                chunk_text = chunk_text[:18000] + "\n[... truncated ...]"
            chunks.append((start + 1, end, chunk_text))
        doc.close()
    except Exception as e:
        print(f"Error extracting PDF text for chunked check: {e}")
        return []

    primary_jurisdiction = jurisdiction or (jurisdictions_detected[0] if jurisdictions_detected else None)
    checklist = get_checklist_for_jurisdiction(primary_jurisdiction)
    items_parsed = parse_checklist_items(checklist)
    all_items_list = "\n".join(
        [f"  {i+1}. {item_text} {'*REQUIRED*' if is_required else ''}"
         for i, (item_text, _, is_required) in enumerate(items_parsed)]
    )

    def check_one_chunk(args):
        page_start, page_end, chunk_text = args
        try:
            initialize_gemini()
            model = genai.GenerativeModel('gemini-3-flash-preview')
            prompt = f"""You are a compliance analyst. Check this DOCUMENT EXCERPT (pages {page_start}-{page_end}) against the checklist. You MUST flag red-flag violations.

CHECKLIST (required items must appear; prohibited content must not appear):
{all_items_list}

DOCUMENT EXCERPT (pages {page_start}-{page_end}):
{chunk_text}

RED FLAGS - you MUST flag these if they appear in the excerpt (with exact_text = exact quote from document):
- "guaranteed" return/profit/gain/growth/yield, or "we guarantee"
- Specific percentage return or gain (e.g. "10% return", "100% gain", "X% growth")
- "promise" of return/profit/gain
- Forecast of future price of a security
- False or misleading statements
- Marketing materials must not include these; if present, output them in violations with exact_text.

RULES: Flag (1) any red flags above, (2) promises of specific returns/gains, (3) false or misleading statements, (4) missing required statements in this excerpt. For each violation provide exact_text: an EXACT quote (15-150 chars) from the excerpt.

Respond in this EXACT JSON format:
{{"violations": [{{"violation": "description", "exact_text": "exact quote from document"}}], "missing_required": [{{"element": "description", "checklist_reference": "which item"}}]}}
If no violations: {{"violations": [], "missing_required": []}}
"""
            response = model.generate_content(
                prompt,
                generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40, "max_output_tokens": 1024}
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            return data, page_start, page_end
        except Exception as e:
            print(f"Chunk check error (pages {page_start}-{page_end}): {e}")
            return ({"violations": [], "missing_required": []}, page_start, page_end)

    all_violations = []
    all_missing = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(check_one_chunk, c) for c in chunks]
        for future in as_completed(futures):
            try:
                data, ps, pe = future.result()
                for v in data.get("violations", []):
                    if isinstance(v, dict):
                        all_violations.append(ViolationDetail(
                            violation=v.get("violation", ""),
                            exact_text=v.get("exact_text"),
                        ))
                    else:
                        all_violations.append(ViolationDetail(violation=str(v), exact_text=None))
                for m in data.get("missing_required", []):
                    elem = m.get("element", "") if isinstance(m, dict) else str(m)
                    ref = m.get("checklist_reference", "") if isinstance(m, dict) else ""
                    all_missing.append(MissingPhrase(phrase=elem, required=True, reason=ref, exact_highlight_text=None))
            except Exception:
                pass

    checklist_items = [
        ChecklistItem(item=item_text, section=section, is_required=is_required, is_compliant=True, missing_details="", exact_highlight_text=None)
        for item_text, section, is_required in items_parsed
    ]
    violations = [v.violation for v in all_violations]
    return [ChecklistResult(
        jurisdiction=None,
        checklist_items=checklist_items,
        missing_required=all_missing,
        violations=violations,
        violation_details=all_violations,
    )]


def check_entire_document_compliance(
    pdf_bytes: bytes,
    jurisdictions_detected: List[str],
    jurisdiction: Optional[str] = None
) -> List[ChecklistResult]:
    """
    Check the ENTIRE document (all pages) against the compliance checklist.
    This checks the full document content, not just disclaimers.
    
    Args:
        pdf_bytes: PDF file bytes
        jurisdictions_detected: List of detected jurisdictions
        jurisdiction: Optional jurisdiction hint
        
    Returns:
        List of ChecklistResult objects for the entire document
    """
    from app.models import ChecklistResult
    from app.services.compliance_checklist import get_checklist_for_jurisdiction, parse_checklist_items
    import tempfile
    import os
    
    if not settings.GEMINI_API_KEY:
        return []
    
    try:
        initialize_gemini()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Get checklist for primary jurisdiction or general
        primary_jurisdiction = jurisdiction or (jurisdictions_detected[0] if jurisdictions_detected else None)
        checklist = get_checklist_for_jurisdiction(primary_jurisdiction)
        items_parsed = parse_checklist_items(checklist)
        all_items_list = "\n".join([f"  {i+1}. {item_text} {'*REQUIRED*' if is_required else ''}" 
                                   for i, (item_text, _, is_required) in enumerate(items_parsed)])
        
        prompt = f"""You are a compliance analyst checking the ENTIRE marketing document against regulatory requirements. This is a LEGAL matter - be DETERMINISTIC, THOROUGH, and CONSISTENT.

COMPLIANCE CHECKLIST:
{all_items_list}

JURISDICTIONS DETECTED: {', '.join(jurisdictions_detected) if jurisdictions_detected else 'General'}

TASK:
1. Read through EVERY page of the document systematically - do NOT skip any pages
2. Check the ENTIRE document (all pages, all text) against the checklist above
3. Look for violations in the main content, disclaimers, headers, footers, charts, tables - EVERYWHERE
4. Check if required items (marked *REQUIRED*) are present anywhere in the document
5. Flag violations found anywhere in the document

CRITICAL FOR LARGE DOCUMENTS (50+ pages):
- You MUST process ALL pages, even if the document is very long
- Do NOT stop after the first few pages
- Check every section, appendix, and page systematically
- Extract violations from ALL pages, not just the beginning
- If the document has many pages, take your time to read through everything

DETERMINISTIC RULES:
1. For EACH numbered checklist item, check if it is present/compliant in the document
2. Use EXACT text matching - search for the exact phrases/requirements mentioned
3. For items marked *REQUIRED*, they MUST be present - mark non-compliant if clearly absent
4. For violations, check the ENTIRE document for:
   - Promises of specific returns/gains (e.g., "100% gain", "guaranteed return", "promise of X%")
   - False or misleading statements
   - Forecasts of future prices without proper disclaimers
   - Unfair promises or guarantees
   - Missing risk warnings (if required)
   - Missing past performance disclaimers (if past performance is mentioned)
5. Be CONSERVATIVE - if unsure or wording is similar, mark as compliant
6. Do NOT interpret or infer - use literal matching only

VIOLATION EXAMPLES (flag these if found ANYWHERE in document):
- "100% gain" or "guaranteed 100% return" → VIOLATION: promise of specific return
- "We promise X% return" → VIOLATION: promise without proper disclaimers
- "Guaranteed profit" → VIOLATION: misleading guarantee
- Any specific percentage return promise without proper risk warnings

Respond in this EXACT JSON format:
{{
  "checklist_items": [
    {{
      "item": "exact checklist item text",
      "section": "section name",
      "is_compliant": true/false,
      "missing_details": "details if not compliant, empty string if compliant"
    }}
  ],
  "missing_required": [
    {{
      "element": "description of missing required element",
      "checklist_reference": "which checklist item it violates"
    }}
  ],
  "violations": [
    {{
      "violation": "description of violation with exact text from document",
      "checklist_reference": "which checklist item it violates",
      "page_reference": "page number if known"
    }}
  ]
}}"""
        
        # Save PDF bytes to temporary file for Gemini upload
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_file_path = tmp_file.name
        
        try:
            # Upload PDF file to Gemini
            uploaded_file = genai.upload_file(path=tmp_file_path, mime_type="application/pdf")
            
            try:
                # Generate content with PDF
                response = model.generate_content(
                    [prompt, uploaded_file],
                    generation_config={
                        "temperature": 0.0,
                        "top_p": 0.95,
                        "top_k": 40,
                        "max_output_tokens": 8192  # Increased for large PDFs
                    }
                )
                response_text = response.text.strip()
            finally:
                # Clean up uploaded file from Gemini
                try:
                    genai.delete_file(uploaded_file.name)
                except:
                    pass
        finally:
            # Clean up temporary file
            if os.path.exists(tmp_file_path):
                try:
                    os.unlink(tmp_file_path)
                except:
                    pass
        
        # Parse JSON
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"JSON parse error in check_entire_document_compliance: {e}")
            print(f"Response: {response_text[:500]}")
            return []
        
        # Build checklist items with status
        checklist_items_dict = {item["item"]: item for item in data.get("checklist_items", [])}
        checklist_items = []
        
        for item_text, section, is_required in items_parsed:
            item_data = checklist_items_dict.get(item_text, {})
            checklist_items.append(ChecklistItem(
                item=item_text,
                section=section,
                is_required=is_required,
                is_compliant=item_data.get("is_compliant", False),
                missing_details=item_data.get("missing_details", "")
            ))
        
        missing_required = [
            MissingPhrase(
                phrase=item["element"],
                required=True,
                reason=f"Required by checklist: {item.get('checklist_reference', 'N/A')}"
            )
            for item in data.get("missing_required", [])
        ]
        
        violations = [
            item["violation"] for item in data.get("violations", [])
        ]
        
        # Return as a single ChecklistResult for the entire document
        return [ChecklistResult(
            jurisdiction=None,  # Document-wide check
            checklist_items=checklist_items,
            missing_required=missing_required,
            violations=violations
        )]
        
    except Exception as e:
        print(f"Error checking entire document compliance: {e}")
        return []


def check_all_disclaimers_compliance(
    all_detected: List[DetectedDisclaimer],
    jurisdiction: Optional[str] = None
) -> List[ChecklistResult]:
    """
    Check compliance for all detected disclaimers in a SINGLE optimized LLM call.
    This is much faster than checking each disclaimer separately.
    
    Args:
        all_detected: All detected disclaimers
        jurisdiction: Optional jurisdiction hint
        
    Returns:
        List of ChecklistResult objects
    """
    from app.models import ChecklistResult
    from app.services.compliance_checklist import get_checklist_for_jurisdiction, parse_checklist_items
    
    if not settings.GEMINI_API_KEY or not all_detected:
        return []
    
    try:
        initialize_gemini()
        # Use deterministic generation config for legal compliance
        generation_config = {
            "temperature": 0.0,  # Completely deterministic
            "top_p": 0.95,
            "top_k": 40,
        }
        model = genai.GenerativeModel('gemini-3-flash-preview', generation_config=generation_config)
        
        # Build prompt for all disclaimers at once - OPTIMIZED: shorter text, only required items
        disclaimers_section = ""
        for idx, detected in enumerate(all_detected):
            jur_name = detected.jurisdiction.value if detected.jurisdiction else "General"
            
            # Only apply region-specific checklist if disclaimer has a specific jurisdiction
            if detected.jurisdiction is None or jur_name == "General":
                # General disclaimer - only general requirements
                checklist = get_checklist_for_jurisdiction(None)
            else:
                # Region-specific disclaimer - general + region-specific requirements
                checklist = get_checklist_for_jurisdiction(jur_name)
            
            # Parse checklist items - include ALL items (required and optional) for comprehensive checking
            items_parsed = parse_checklist_items(checklist)
            all_items_list = "\n".join([f"  {i+1}. {item_text} {'*REQUIRED*' if is_required else ''}" 
                                       for i, (item_text, _, is_required) in enumerate(items_parsed)])
            
            # Include full disclaimer text, but limit to 5000 chars to avoid token limits for very long disclaimers
            disclaimer_text = detected.text[:5000] if len(detected.text) > 5000 else detected.text
            if len(detected.text) > 5000:
                disclaimer_text += "\n[... (disclaimer continues, full text checked by LLM in PDF) ...]"
            
            disclaimers_section += f"""
DISCLAIMER {idx + 1} - {jur_name} JURISDICTION:
Checklist Items:
{all_items_list}

Disclaimer Text:
{disclaimer_text}

---
"""
        
        prompt = f"""You are a compliance analyst checking multiple disclaimers against regulatory requirements. This is a LEGAL matter - be DETERMINISTIC, THOROUGH, and CONSISTENT.

{disclaimers_section}

CRITICAL FOR LARGE DOCUMENTS:
- If processing a large document (50+ pages), ensure you check ALL disclaimers completely
- Do NOT skip any disclaimers or truncate your analysis
- Process each disclaimer thoroughly against its checklist

DETERMINISTIC RULES (FOLLOW EXACTLY FOR EACH DISCLAIMER):
1. For EACH numbered checklist item above, check if it is present/compliant in the disclaimer text
2. Use EXACT text matching - search for the exact phrases/requirements mentioned in each item
3. For items marked *REQUIRED*, they MUST be present - mark non-compliant ONLY if clearly absent
4. For violations, check the ENTIRE disclaimer text for:
   - Promises of specific returns/gains (e.g., "100% gain", "guaranteed return", "promise of X%")
   - False or misleading statements
   - Forecasts of future prices without proper disclaimers
   - Unfair promises or guarantees
   - Missing risk warnings (if required)
   - Missing past performance disclaimers (if past performance is mentioned)
5. Be CONSERVATIVE - if unsure or wording is similar, mark as compliant
6. Do NOT interpret or infer - use literal matching only
7. If disclaimer meets requirements, mark ALL items as compliant

VIOLATION EXAMPLES (flag these if found in disclaimer text):
- "100% gain" or "guaranteed 100% return" → VIOLATION: promise of specific return
- "We promise X% return" → VIOLATION: promise without proper disclaimers
- "Guaranteed profit" → VIOLATION: misleading guarantee
- Any specific percentage return promise without proper risk warnings

For EACH disclaimer above, provide:
1. Checklist items status - check each numbered item against disclaimer text using exact matching
2. Missing required elements - ONLY items marked *REQUIRED* that are clearly absent
3. Violations - flag promises of returns/gains, false/misleading statements explicitly from checklist

Respond in this EXACT JSON format:
{{
  "results": [
    {{
      "disclaimer_index": 1,
      "jurisdiction": "UAE",
      "checklist_items": [
        {{
          "item": "exact checklist item text",
          "section": "section name",
          "is_compliant": true/false,
          "missing_details": "details if not compliant, empty string if compliant"
        }}
      ],
      "missing_required": [
        {{
          "element": "description of missing element",
          "checklist_reference": "which checklist item"
        }}
      ],
      "violations": ["violation description"]
    }}
  ]
}}"""
        
        # Use deterministic generation with temperature=0
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40}
        )
        response_text = response.text.strip()
        
        # Parse JSON
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            print(f"JSON parse error in check_all_disclaimers_compliance: {e}")
            print(f"Response: {response_text[:500]}")
            # Return empty results on parse error to be conservative
            return []
        
        checklist_results = []
        for result_data in data.get("results", []):
            idx = result_data.get("disclaimer_index", 1) - 1
            if idx < len(all_detected):
                detected = all_detected[idx]
                jur_name = detected.jurisdiction.value if detected.jurisdiction else None
                
                # Parse checklist items
                checklist_text = get_checklist_for_jurisdiction(jur_name)
                items_parsed = parse_checklist_items(checklist_text)
                items_dict = {item["item"]: item for item in result_data.get("checklist_items", [])}
                
                checklist_items = []
                for item_text, section, is_required in items_parsed:
                    item_data = items_dict.get(item_text, {})
                    checklist_items.append(ChecklistItem(
                        item=item_text,
                        section=section,
                        is_required=is_required,
                        is_compliant=item_data.get("is_compliant", False),
                        missing_details=item_data.get("missing_details", "")
                    ))
                
                missing_phrases = [
                    MissingPhrase(
                        phrase=item["element"],
                        required=True,
                        reason=f"Required by checklist: {item.get('checklist_reference', 'N/A')}"
                    )
                    for item in result_data.get("missing_required", [])
                ]
                
                violations = result_data.get("violations", [])
                
                checklist_results.append(ChecklistResult(
                    jurisdiction=detected.jurisdiction,
                    checklist_items=checklist_items,
                    missing_required=missing_phrases,
                    violations=violations
                ))
        
        return checklist_results
        
    except Exception as e:
        print(f"Error in optimized checklist check: {e}, falling back to individual checks")
        # Fallback to individual checks if batch fails
        checklist_results = []
        for detected in all_detected:
            jur = detected.jurisdiction.value if detected.jurisdiction else jurisdiction
            missing_phrases, violations, checklist_items = check_checklist_compliance_with_items(
                detected, jur
            )
            checklist_results.append(ChecklistResult(
                jurisdiction=detected.jurisdiction,
                checklist_items=checklist_items,
                missing_required=missing_phrases,
                violations=violations
            ))
        return checklist_results


def get_footnote_section_from_llm(pdf_bytes: bytes) -> Dict[str, str]:
    """
    Use the LLM to extract the footnote section from the document (like pasting the doc into chat).
    Returns a dict mapping each footnote label (e.g. "1", "2", "*") to its full definition text.
    If there is no footnote section, returns {}.
    """
    if not settings.GEMINI_API_KEY or not pdf_bytes:
        return {}
    import os
    import tempfile
    prompt = """You are extracting the FOOTNOTE SECTION from this PDF document.

TASK: Find the footnote section (usually at the end of the document or bottom of pages) where footnotes are DEFINED. Look for:
- Numbered lines like "1. ...", "2. ...", "11. ..." or "1) ...", "2) ..."
- Asterisk footnotes like "* ..." or "** ..."
- Any block of text that clearly lists footnote definitions

For each footnote you find, record its LABEL (the number or asterisk(s)) and the FULL TEXT of that footnote definition.
If the document has NO footnote section at all, return an empty object.

Respond with ONLY a JSON object (no markdown, no explanation). Format:
{"1": "full text of footnote 1 here", "2": "full text of footnote 2 here", "*": "full text of asterisk footnote"}
Use the exact label as key (e.g. "1", "2", "11", "*"). If no footnotes: {}}"""

    try:
        initialize_gemini()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_file_path = tmp_file.name
        try:
            uploaded_file = genai.upload_file(path=tmp_file_path, mime_type="application/pdf")
            try:
                response = model.generate_content(
                    [prompt, uploaded_file],
                    generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40, "max_output_tokens": 8192}
                )
                raw = (response.text or "").strip()
            finally:
                try:
                    genai.delete_file(uploaded_file.name)
                except Exception:
                    pass
        finally:
            if os.path.exists(tmp_file_path):
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            if k and v is not None and isinstance(v, str) and v.strip():
                out[str(k).strip()] = v.strip()
        return out
    except Exception as e:
        print(f"LLM footnote section extraction error: {e}")
        return {}


def get_footnote_references_from_llm(pdf_bytes: bytes) -> List[Dict]:
    """
    Ask the LLM to identify footnote REFERENCES in the body only. Refs are validated later
    against the document's footnote section (if any). Be conservative to avoid false positives.

    Returns:
        List of {"page": int, "ref_text": str} e.g. [{"page": 5, "ref_text": "11,12"}, ...]
    """
    if not settings.GEMINI_API_KEY or not pdf_bytes:
        return []
    import os
    import tempfile
    prompt = """You are analyzing a PDF for footnote REFERENCES in the body text only (numbers or asterisks that point to footnotes). The document may or may not have a footnote section at the bottom.

STRICT RULES - only report when you are confident it is a footnote reference:
- Must look like a REFERENCE: superscript, or noticeably smaller font than the main text, often immediately after a word or figure.
- Strong signals: comma-separated numbers (e.g. "11,12", "1,2,3") or asterisk(s) (*) in superscript/small style.
- Single small numbers (e.g. "1", "12") only if they are clearly superscript/small and attached to preceding text as a reference, not standalone data.

Do NOT report (these are NOT footnote references):
- Percentages or multipliers (e.g. 28%, 25%, 1.6x, 1.4x).
- Numbers in tables that are data (values, amounts, dates).
- List numbers (1. 2. 3.) or section numbers ("Section 1", "page 11").
- Standalone numbers in narrative that are part of the sentence (years, counts, etc.).
- The footnote DEFINITIONS at the bottom of the page or end of document (only report references in the main body).
When in doubt, do NOT report. Prefer missing a ref over flagging something that is not a ref.

For each reference you find, report PAGE (1-based) and the EXACT reference text as it appears (e.g. "11,12", "3", "*").
Respond with ONLY this JSON (no markdown):
{"references": [{"page": 1, "ref_text": "11,12"}, ...]}
If no footnote references found: {"references": []}}"""

    try:
        initialize_gemini()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_file_path = tmp_file.name
        try:
            uploaded_file = genai.upload_file(path=tmp_file_path, mime_type="application/pdf")
            try:
                response = model.generate_content(
                    [prompt, uploaded_file],
                    generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40, "max_output_tokens": 4096}
                )
                raw = (response.text or "").strip()
            finally:
                try:
                    genai.delete_file(uploaded_file.name)
                except Exception:
                    pass
        finally:
            if os.path.exists(tmp_file_path):
                try:
                    os.unlink(tmp_file_path)
                except Exception:
                    pass
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        refs = data.get("references") or []
        out = []
        for r in refs:
            page = r.get("page")
            ref_text = (r.get("ref_text") or "").strip()
            if page is not None and ref_text:
                out.append({"page": int(page), "ref_text": ref_text})
        return out
    except Exception as e:
        print(f"LLM footnote reference detection error: {e}")
        return []


def get_footnote_issues_from_llm(
    footnotes: Dict[str, str],
    jurisdictions_detected: Optional[List[str]] = None,
) -> List[FootnoteIssue]:
    """
    Send the document's footnotes to the LLM and get back compliance/content issues.
    Runs in a separate call from disclaimer and document checks.
    
    Returns:
        List of FootnoteIssue (page=1 for content-level issues, issue_type llm_footnote_*).
    """
    if not settings.GEMINI_API_KEY or not footnotes:
        return []
    def _footnote_sort_key(item):
        k = item[0]
        if k.isdigit():
            return (0, int(k), k)
        return (1, 0, k)
    footnote_text = "\n".join(f'[{k}] {v}' for k, v in sorted(footnotes.items(), key=_footnote_sort_key))
    if len(footnote_text) > 12000:
        footnote_text = footnote_text[:12000] + "\n[... truncated ...]"
    jurisdictions_str = ", ".join(jurisdictions_detected) if jurisdictions_detected else "General"
    prompt = f"""You are a compliance analyst reviewing the FOOTNOTES of a marketing document for financial products. These footnotes are from a document that may target: {jurisdictions_str}.

FOOTNOTES (reference number followed by text):
{footnote_text}

TASK: Identify any issues with the footnotes that could affect regulatory compliance or clarity. Consider:
1. Compliance: Do any footnotes promise returns, guarantee performance, or make misleading claims?
2. Consistency: Do footnotes contradict the main disclaimer text or regulatory requirements?
3. Completeness: Are risk warnings or required disclosures missing in a footnote where they would be expected?
4. Wording: Misleading, ambiguous, or non-compliant language in footnote text.
5. Numbering: Gaps or inconsistencies in footnote numbering (e.g. 1, 2, 4 with 3 missing) if evident from the list.
6. Legal/regulatory: Any wording that could be problematic for the jurisdictions mentioned.

Be DETERMINISTIC. Only flag real issues; do not nitpick style. If there are no issues, return an empty list.

Respond with ONLY this JSON (no markdown, no explanation):
{{"issues": [{{"issue_type": "short_snake_type", "message": "One clear sentence describing the issue.", "footnote_ref": "number or * if applicable"}}]}}

If no issues: {{"issues": []}}"""

    try:
        initialize_gemini()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        response = model.generate_content(
            prompt,
            generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40, "max_output_tokens": 1024}
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        issues = data.get("issues") or []
        result = []
        for item in issues:
            msg = (item.get("message") or "").strip()
            if not msg:
                continue
            ref = item.get("footnote_ref")
            if isinstance(ref, (int, float)):
                ref = str(int(ref))
            result.append(FootnoteIssue(
                page=1,
                issue_type=item.get("issue_type") or "llm_footnote_content",
                message=msg,
                reference=ref if isinstance(ref, str) and ref else None,
            ))
        return result
    except Exception as e:
        print(f"LLM footnote check error: {e}")
        return []


def generate_analysis_result(
    detected: Optional[DetectedDisclaimer],
    all_detected: List[DetectedDisclaimer],
    jurisdictions_detected: List[str],
    comparison_results: List[ComparisonResult],
    jurisdiction: Optional[str] = None,
    pdf_bytes: Optional[bytes] = None
) -> AnalysisResult:
    """
    Generate the complete analysis result based ONLY on checklist compliance.
    
    Args:
        detected: Detected disclaimer
        all_detected: All detected disclaimers
        comparison_results: Comparison results (for reference only)
        jurisdiction: Optional jurisdiction
        pdf_bytes: PDF bytes for entire document checking
        
    Returns:
        Complete AnalysisResult object
    """
    # 1) Fast red-flag scan (no LLM). If we find violations, we do NOT run chunked doc LLM (each page passed to LLM at most once).
    red_flag_details: List[ViolationDetail] = []
    if pdf_bytes:
        red_flag_details = deduplicate_violation_details(scan_document_red_flags(pdf_bytes))

    # 2) Chunked document compliance only when regex found nothing (avoid sending same pages to LLM twice)
    document_checklist_results: List[ChecklistResult] = []
    if pdf_bytes and not red_flag_details:
        document_checklist_results = check_entire_document_compliance_chunked(
            pdf_bytes, jurisdictions_detected, jurisdiction
        )

    disclaimer_checklist_results = check_all_disclaimers_compliance_multi_call(all_detected, jurisdiction)

    # Merge red-flag scan into document-wide result; dedupe so same violation appears once
    if red_flag_details:
        doc_result = next((r for r in document_checklist_results if r.jurisdiction is None), None)
        if doc_result is not None:
            merged_vd = deduplicate_violation_details(list(doc_result.violation_details) + red_flag_details)
            merged_v = [v.violation for v in merged_vd]
            document_checklist_results = [
                ChecklistResult(
                    jurisdiction=r.jurisdiction,
                    checklist_items=r.checklist_items,
                    missing_required=r.missing_required,
                    violations=merged_v if r.jurisdiction is None else r.violations,
                    violation_details=merged_vd if r.jurisdiction is None else r.violation_details,
                )
                for r in document_checklist_results
            ]
        else:
            document_checklist_results.append(ChecklistResult(
                jurisdiction=None,
                checklist_items=[],
                missing_required=[],
                violations=[v.violation for v in red_flag_details],
                violation_details=red_flag_details,
            ))

    # Deduplicate within each result, then drop disclaimer violations that duplicate document-level (same exact_text)
    doc_exact_keys = set()
    for res in document_checklist_results:
        for v in res.violation_details:
            k = _normalize_exact_key(v.exact_text) if v.exact_text else ""
            if k:
                doc_exact_keys.add(k)
    checklist_results = []
    for res in document_checklist_results:
        vd_deduped = deduplicate_violation_details(list(res.violation_details))
        checklist_results.append(ChecklistResult(
            jurisdiction=res.jurisdiction,
            checklist_items=res.checklist_items,
            missing_required=res.missing_required,
            violations=[v.violation for v in vd_deduped],
            violation_details=vd_deduped,
        ))
    for res in disclaimer_checklist_results:
        vd_deduped = deduplicate_violation_details(list(res.violation_details))
        vd_no_dup = [v for v in vd_deduped if not (v.exact_text and _normalize_exact_key(v.exact_text) in doc_exact_keys)]
        doc_exact_keys.update(_normalize_exact_key(v.exact_text) for v in vd_no_dup if v.exact_text)
        checklist_results.append(ChecklistResult(
            jurisdiction=res.jurisdiction,
            checklist_items=res.checklist_items,
            missing_required=res.missing_required,
            violations=[v.violation for v in vd_no_dup],
            violation_details=vd_no_dup,
        ))

    all_missing_phrases = []
    all_violations = []
    seen_violation_key: set = set()
    for res in checklist_results:
        all_missing_phrases.extend(res.missing_required)
        for v in res.violation_details:
            k = _normalize_exact_key(v.exact_text) if v.exact_text else ("v:" + (v.violation or "")[:80])
            if k not in seen_violation_key:
                seen_violation_key.add(k)
                all_violations.append(v.violation)
    
    # Use aggregated results
    missing_phrases = all_missing_phrases
    checklist_violations = all_violations
    
    # Classify risk level based ONLY on checklist
    risk_level = classify_risk_level(missing_phrases, checklist_violations)
    
    # Determine approval status based ONLY on checklist
    is_approved = determine_approval_status(risk_level, missing_phrases, checklist_violations)
    
    best_match = comparison_results[0] if comparison_results else None
    
    explanation = generate_explanation(
        is_approved, risk_level, best_match, missing_phrases, detected, checklist_violations
    )
    summary_blurb = generate_summary_blurb(
        is_approved, missing_phrases, checklist_violations, detected
    )
    llm_suggestions = None

    # Footnote and formatting checks (footnotes dict, locations for highlighting, footnote ref issues, unusual color, existing highlights)
    footnotes_dict = {}
    footnote_locations = {}
    footnote_issues_list = []
    formatting_issues_list = []
    if pdf_bytes:
        from app.services.footnotes import run_footnote_and_formatting_checks, find_ref_bbox_on_page
        _py_footnotes, footnote_locations, _fn_issues_python, color_issues, highlight_issues = run_footnote_and_formatting_checks(pdf_bytes)
        # Use LLM to extract footnote section and references (like putting doc in chat), then validate
        footnotes_dict = get_footnote_section_from_llm(pdf_bytes)
        if not footnotes_dict and _py_footnotes:
            footnotes_dict = _py_footnotes  # fallback to Python extraction if LLM returns empty
        has_footnote_section = bool(footnotes_dict)
        llm_refs = get_footnote_references_from_llm(pdf_bytes)
        footnote_issues_list = []
        for item in llm_refs:
            page_no = item.get("page", 1)
            ref_text = (item.get("ref_text") or "").strip()
            if not ref_text:
                continue
            refs_split = [s.strip() for s in ref_text.split(",") if s.strip()]
            for ref in refs_split:
                if has_footnote_section:
                    # Document has a footnote section: only flag refs that don't have a definition
                    if ref not in footnotes_dict:
                        bbox = find_ref_bbox_on_page(pdf_bytes, page_no, ref) or find_ref_bbox_on_page(pdf_bytes, page_no, ref_text)
                        footnote_issues_list.append(FootnoteIssue(
                            page=page_no,
                            issue_type="footnote_reference_missing",
                            message=f"Footnote reference '{ref}' has no matching footnote in this document.",
                            reference=ref,
                            bbox=bbox,
                        ))
                else:
                    # No footnote section: flag only if ref looks like a footnote ref (not e.g. year or big number)
                    if ref.isdigit() and len(ref) > 3:
                        continue  # e.g. 2024, 12345 - likely not a footnote ref
                    if not (ref.isdigit() or ref.strip("*") == ""):
                        continue  # allow digits and asterisk(s) only
                    bbox = find_ref_bbox_on_page(pdf_bytes, page_no, ref) or find_ref_bbox_on_page(pdf_bytes, page_no, ref_text)
                    footnote_issues_list.append(FootnoteIssue(
                        page=page_no,
                        issue_type="footnote_reference_no_section",
                        message="Footnote reference with no footnote section in document.",
                        reference=ref,
                        bbox=bbox,
                    ))
        # LLM review of footnote content (compliance, consistency, wording)
        if footnotes_dict:
            llm_footnote_issues = get_footnote_issues_from_llm(footnotes_dict, jurisdictions_detected)
        else:
            llm_footnote_issues = []
        footnote_issues_list = footnote_issues_list + llm_footnote_issues
        for u in color_issues:
            bbox = u.get("bbox")
            formatting_issues_list.append(FormattingIssue(
                page=u["page"], issue_type="unusual_color", message="Red text (possible edit remnant).",
                text=u.get("text"), color_hex=u.get("color_hex"),
                bbox=list(bbox) if bbox else None,
            ))
        for u in highlight_issues:
            formatting_issues_list.append(FormattingIssue(
                page=u["page"], issue_type="existing_highlight", message=u["message"], text=None
            ))

    return AnalysisResult(
        is_approved=is_approved,
        risk_level=risk_level,
        detected_disclaimer=detected,
        all_detected_disclaimers=all_detected,
        jurisdictions_detected=jurisdictions_detected,
        comparison_results=comparison_results[:5],
        missing_required_phrases=missing_phrases,
        checklist_results=checklist_results,
        closest_match_id=best_match.approved_disclaimer_id if best_match else None,
        explanation=explanation,
        llm_suggestions=llm_suggestions,
        summary_blurb=summary_blurb,
        footnotes=footnotes_dict if footnotes_dict else None,
        footnotes_locations=footnote_locations if footnote_locations else None,
        footnote_issues=footnote_issues_list,
        formatting_issues=formatting_issues_list,
    )
