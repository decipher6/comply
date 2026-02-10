# app/services/recommendations.py
# Note: google.generativeai is deprecated in favor of google.genai
# Keeping current package for now as it still works. Consider migrating to google.genai in future.
import google.generativeai as genai
from app.config import settings
from app.models import DetectedDisclaimer
from typing import List, Dict, Optional
import re
import json


def initialize_gemini():
    """Initialize Gemini API client."""
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set in environment variables")
    genai.configure(api_key=settings.GEMINI_API_KEY)


class IssueRecommendation:
    """Represents a specific issue found in the disclaimer with recommendation."""
    def __init__(self, problematic_text: str, issue_type: str, recommendation: str, severity: str = "MEDIUM"):
        self.problematic_text = problematic_text  # The exact text that's problematic
        self.issue_type = issue_type  # e.g., "missing_phrase", "unclear_wording", "risky_language"
        self.recommendation = recommendation  # What should be done
        self.severity = severity  # HIGH, MEDIUM, LOW


def get_detailed_recommendations(
    detected: DetectedDisclaimer,
    pdf_bytes: bytes,
    comparison_results: List = None
) -> List[IssueRecommendation]:
    """
    Get detailed LLM recommendations for specific parts of the disclaimer that need fixing.
    Returns structured recommendations with the exact text that's problematic.
    
    Args:
        detected: Detected disclaimer
        pdf_bytes: PDF file bytes (to pass to LLM for context)
        comparison_results: Comparison results with approved disclaimers
        
    Returns:
        List of IssueRecommendation objects
    """
    if not settings.GEMINI_API_KEY or not detected:
        return []
    
    try:
        initialize_gemini()
        model = genai.GenerativeModel('gemini-3-flash-preview')
        
        # Prepare context
        comparison_info = ""
        if comparison_results and len(comparison_results) > 0:
            best = comparison_results[0]
            comparison_info = f"""
Comparison with approved disclaimer:
- Similarity: {best.similarity_score:.1%}
- Missing phrases: {', '.join(best.missing_phrases[:5])}
- Matched phrases: {', '.join(best.matched_phrases[:5])}
"""
        
        # Get compliance checklist for jurisdiction
        from app.services.compliance_checklist import get_checklist_for_jurisdiction
        jurisdiction_name = detected.jurisdiction.value if detected.jurisdiction else None
        checklist = get_checklist_for_jurisdiction(jurisdiction_name)
        
        prompt = f"""You are a compliance expert analyzing a marketing disclaimer against regulatory requirements. Be DETERMINISTIC and SKEPTICAL - only flag actual compliance issues, not stylistic preferences.

COMPLIANCE CHECKLIST:
{checklist}

Detected Disclaimer Text:
{detected.text}

Jurisdiction: {jurisdiction_name or 'Unknown'}

{comparison_info}

ANALYSIS INSTRUCTIONS:
1. Review the disclaimer against the compliance checklist above
2. Identify ONLY actual compliance violations or missing required elements
3. Do NOT flag:
   - Minor wording variations that still meet requirements
   - Stylistic differences
   - Optional elements that aren't required
4. DO flag:
   - Missing required statements (marked with * in checklist)
   - False or misleading statements
   - Missing jurisdiction-specific requirements
   - Unclear or ambiguous language that could mislead
   - Missing risk warnings or past performance disclaimers

For each ACTUAL issue found:
- Quote the EXACT problematic text (or note if text is missing)
- Identify the issue type: missing_required_statement, false_misleading_statement, unclear_wording, missing_risk_warning, missing_past_performance_disclaimer, wrong_jurisdiction, incomplete_information
- Provide SPECIFIC recommendation referencing the checklist requirement
- Assign severity: HIGH (regulatory violation), MEDIUM (missing required element), LOW (unclear but not clearly violating)

Respond in this EXACT JSON format (no markdown, just JSON):
{{
  "issues": [
    {{
      "problematic_text": "exact text from disclaimer or 'MISSING' if not present",
      "issue_type": "missing_required_statement",
      "recommendation": "specific recommendation referencing checklist requirement X",
      "severity": "HIGH"
    }}
  ]
}}

If the disclaimer meets all requirements, return: {{"issues": []}}

Be accurate and conservative - only flag real compliance issues."""
        
        # Upload PDF for context
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
            tmp_file.write(pdf_bytes)
            tmp_file_path = tmp_file.name
        
        try:
            uploaded_file = genai.upload_file(path=tmp_file_path, mime_type="application/pdf")
            try:
                # Use deterministic generation with temperature=0
                response = model.generate_content(
                    [prompt, uploaded_file],
                    generation_config={"temperature": 0.0, "top_p": 0.95, "top_k": 40}
                )
                response_text = response.text
            finally:
                try:
                    genai.delete_file(uploaded_file.name)
                except:
                    pass
        finally:
            if os.path.exists(tmp_file_path):
                try:
                    os.unlink(tmp_file_path)
                except:
                    pass
        
        # Parse JSON response
        # Clean up response text (remove markdown code blocks if present)
        response_text = response_text.strip()
        if response_text.startswith("```json"):
            response_text = response_text[7:]
        if response_text.startswith("```"):
            response_text = response_text[3:]
        if response_text.endswith("```"):
            response_text = response_text[:-3]
        response_text = response_text.strip()
        
        try:
            data = json.loads(response_text)
            issues = data.get("issues", [])
            
            recommendations = []
            for issue in issues:
                rec = IssueRecommendation(
                    problematic_text=issue.get("problematic_text", ""),
                    issue_type=issue.get("issue_type", "unknown"),
                    recommendation=issue.get("recommendation", ""),
                    severity=issue.get("severity", "MEDIUM")
                )
                recommendations.append(rec)
            
            return recommendations
        except json.JSONDecodeError as e:
            print(f"Failed to parse LLM JSON response: {e}")
            print(f"Response was: {response_text[:500]}")
            return []
        
    except Exception as e:
        print(f"Error getting detailed recommendations: {e}")
        return []
