# app/services/report.py
# Note: google.generativeai is deprecated in favor of google.genai
# Keeping current package for now as it still works. Consider migrating to google.genai in future.
import google.generativeai as genai
import json
from app.config import settings
from app.models import (
    AnalysisResult, DetectedDisclaimer, ComparisonResult,
    MissingPhrase, RiskLevel, ChecklistItem, ChecklistResult
)
from app.services.rules import classify_risk_level, determine_approval_status
from typing import List, Optional


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
        # Use current Gemini Flash model
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
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
        
        response = model.generate_content(prompt)
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
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Get compliance checklist
        jurisdiction_name = detected.jurisdiction.value if detected.jurisdiction else jurisdiction
        checklist = get_checklist_for_jurisdiction(jurisdiction_name)
        checklist_items_parsed = parse_checklist_items(checklist)
        
        prompt = f"""You are a compliance analyst checking a disclaimer against the compliance checklist. ONLY flag issues that are explicitly in the checklist.

COMPLIANCE CHECKLIST:
{checklist}

DETECTED DISCLAIMER:
{detected.text}

JURISDICTION: {jurisdiction_name or 'Unknown'}

CRITICAL RULES:
1. ONLY check against the checklist items above - do NOT add your own requirements
2. For each checklist item, determine if the disclaimer is COMPLIANT or NOT COMPLIANT
3. ONLY flag missing required elements (marked with * in checklist)
4. ONLY flag violations that are explicitly mentioned in the checklist
5. Do NOT make up violations or requirements not in the checklist

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
      "violation": "description of violation",
      "checklist_reference": "which checklist item it violates"
    }}
  ]
}}

If everything is compliant, all items should have "is_compliant": true."""
        
        response = model.generate_content(prompt)
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
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Get compliance checklist
        from app.services.compliance_checklist import get_checklist_for_jurisdiction
        jurisdiction_name = detected.jurisdiction.value if detected.jurisdiction else jurisdiction
        checklist = get_checklist_for_jurisdiction(jurisdiction_name)
        
        prompt = f"""You are a compliance analyst checking a disclaimer against the compliance checklist. ONLY flag issues that are explicitly in the checklist.

COMPLIANCE CHECKLIST:
{checklist}

DETECTED DISCLAIMER:
{detected.text}

JURISDICTION: {jurisdiction_name or 'Unknown'}

CRITICAL: ONLY flag violations that are explicitly mentioned in the checklist above. Do NOT add your own requirements.

CRITICAL RULES:
1. ONLY check against the checklist items above - do NOT add your own requirements
2. ONLY flag missing required elements (marked with * in checklist)
3. ONLY flag violations that are explicitly mentioned in the checklist
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

If everything is compliant, return: {{"missing_required": [], "violations": []}}"""
        
        response = model.generate_content(prompt)
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
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Build prompt for all disclaimers at once
        disclaimers_section = ""
        for idx, detected in enumerate(all_detected):
            jur_name = detected.jurisdiction.value if detected.jurisdiction else "General"
            checklist = get_checklist_for_jurisdiction(jur_name)
            disclaimers_section += f"""
DISCLAIMER {idx + 1} - {jur_name} JURISDICTION:
Checklist for {jur_name}:
{checklist}

Disclaimer Text:
{detected.text[:2000]}

---
"""
        
        prompt = f"""You are a compliance analyst checking multiple disclaimers against regulatory requirements. Be DETERMINISTIC and ACCURATE.

{disclaimers_section}

TASK:
For EACH disclaimer above, check it against its relevant checklist (General requirements + jurisdiction-specific requirements).

For each disclaimer, provide:
1. Checklist items status (compliant/not compliant for each item)
2. Missing required elements
3. Violations

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
        
        response = model.generate_content(prompt)
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


def generate_analysis_result(
    detected: Optional[DetectedDisclaimer],
    all_detected: List[DetectedDisclaimer],
    jurisdictions_detected: List[str],
    comparison_results: List[ComparisonResult],
    jurisdiction: Optional[str] = None
) -> AnalysisResult:
    """
    Generate the complete analysis result based ONLY on checklist compliance.
    
    Args:
        detected: Detected disclaimer
        all_detected: All detected disclaimers
        comparison_results: Comparison results (for reference only)
        jurisdiction: Optional jurisdiction
        
    Returns:
        Complete AnalysisResult object
    """
    # Check compliance for all disclaimers (apply relevant checklist to each)
    checklist_results = check_all_disclaimers_compliance(all_detected, jurisdiction)
    
    # For primary disclaimer, get compliance results
    primary_checklist_result = checklist_results[0] if checklist_results else None
    if primary_checklist_result:
        missing_phrases = primary_checklist_result.missing_required
        checklist_violations = primary_checklist_result.violations
    else:
        # Fallback to old method if no checklist results
        missing_phrases, checklist_violations = check_checklist_compliance(detected, jurisdiction)
    
    # Classify risk level based ONLY on checklist
    risk_level = classify_risk_level(missing_phrases, checklist_violations)
    
    # Determine approval status based ONLY on checklist
    is_approved = determine_approval_status(risk_level, missing_phrases, checklist_violations)
    
    best_match = comparison_results[0] if comparison_results else None
    
    # Generate explanation
    explanation = generate_explanation(
        is_approved, risk_level, best_match, missing_phrases, detected, checklist_violations
    )
    
    # No LLM suggestions - only use checklist results
    llm_suggestions = None
    
    return AnalysisResult(
        is_approved=is_approved,
        risk_level=risk_level,
        detected_disclaimer=detected,  # Primary disclaimer for backward compatibility
        all_detected_disclaimers=all_detected,  # All disclaimers found
        jurisdictions_detected=jurisdictions_detected,  # Jurisdictions detected in document
        comparison_results=comparison_results[:5],  # Top 5 matches (for reference)
        missing_required_phrases=missing_phrases,
        checklist_results=checklist_results,  # Checklist results per jurisdiction
        closest_match_id=best_match.approved_disclaimer_id if best_match else None,
        explanation=explanation,
        llm_suggestions=llm_suggestions
    )
