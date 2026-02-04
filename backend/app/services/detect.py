# app/services/detect.py
# Note: google.generativeai is deprecated in favor of google.genai
# Keeping current package for now as it still works. Consider migrating to google.genai in future.
import google.generativeai as genai
from app.config import settings
from app.models import DetectedDisclaimer, Jurisdiction
from typing import Optional, List, Tuple
import re
import tempfile
import os
import json


def initialize_gemini():
    """Initialize Gemini API client."""
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not set in environment variables")
    genai.configure(api_key=settings.GEMINI_API_KEY)


def detect_jurisdictions_and_disclaimers(pdf_bytes: bytes) -> Tuple[List[str], List[DetectedDisclaimer]]:
    """
    Detect which jurisdictions are mentioned in the document and extract disclaimers for each.
    
    Args:
        pdf_bytes: PDF file bytes
        
    Returns:
        Tuple of (detected_jurisdictions, disclaimers)
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is required. Please set it in your .env file.")
    
    initialize_gemini()
    model = genai.GenerativeModel('gemini-3-flash-preview')
    
    prompt = """You are analyzing a marketing material document for financial products. Your task is:

STEP 1: DETECT JURISDICTIONS
Identify which jurisdictions are mentioned in this document. Look for:
- United Arab Emirates (UAE)
- Dubai International Financial Centre (DIFC)
- Kingdom of Saudi Arabia (KSA)
- Kuwait
- Oman
- Qatar

STEP 2: EXTRACT DISCLAIMERS
For EACH jurisdiction detected, extract the complete disclaimer text that applies to that jurisdiction.
Also extract any general disclaimers (not specific to any jurisdiction).

IMPORTANT:
- Read through ALL pages of the document systematically
- Extract the COMPLETE text of each disclaimer section
- Do NOT truncate or summarize - include ALL text from start to end
- If a disclaimer spans multiple pages, extract ALL of it
- Look for sections that start with phrases like:
  * "For residents of the United Arab Emirates (the 'UAE')"
  * "For residents of the State of Kuwait"
  * "For residents of the State of Qatar"
  * "For residents of the Sultanate of Oman"
  * "For residents of the Kingdom of Saudi Arabia"
  * "For residents of the Dubai International Financial Centre ('DIFC')"
- Also look for general disclaimers that apply to all jurisdictions

Respond in this EXACT JSON format:
{
  "jurisdictions_detected": ["UAE", "Kuwait", "Qatar", ...],
  "disclaimers": [
    {
      "jurisdiction": "UAE",
      "disclaimer_text": "complete full text of the UAE disclaimer"
    },
    {
      "jurisdiction": "Kuwait",
      "disclaimer_text": "complete full text of the Kuwait disclaimer"
    },
    {
      "jurisdiction": "General",
      "disclaimer_text": "complete full text of general disclaimer (if any)"
    }
  ]
}

If no disclaimers found, return:
{
  "jurisdictions_detected": [],
  "disclaimers": []
}

Be extremely thorough - extract every disclaimer completely."""
    
    # Save PDF bytes to temporary file for Gemini upload
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
        tmp_file.write(pdf_bytes)
        tmp_file_path = tmp_file.name
    
    try:
        # Upload PDF file to Gemini
        uploaded_file = genai.upload_file(path=tmp_file_path, mime_type="application/pdf")
        
        try:
            # Generate content with PDF
            response = model.generate_content([prompt, uploaded_file])
            response_text = response.text
            
            # Debug: print response for troubleshooting
            print(f"Gemini Response: {response_text[:1000]}")
            
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
    
    # Parse JSON response
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
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {e}")
        print(f"Response was: {response_text[:500]}")
        return [], []
    
    # Extract jurisdictions
    jurisdictions_detected = data.get("jurisdictions_detected", [])
    
    # Extract disclaimers
    disclaimers = []
    for disc_data in data.get("disclaimers", []):
        jur_name = disc_data.get("jurisdiction", "").strip()
        disclaimer_text = disc_data.get("disclaimer_text", "").strip()
        
        if not disclaimer_text or len(disclaimer_text) < 10:
            continue
        
        # Map jurisdiction name to enum
        detected_jurisdiction = None
        if jur_name.lower() not in ['general', 'unknown', 'all', 'common']:
            detected_jurisdiction = match_jurisdiction_name(jur_name)
        
        disclaimers.append(DetectedDisclaimer(
            text=disclaimer_text,
            jurisdiction=detected_jurisdiction,
            confidence=0.9
        ))
    
    return jurisdictions_detected, disclaimers


def match_jurisdiction_name(jur_name: str) -> Optional[Jurisdiction]:
    """Match jurisdiction name to enum."""
    if not jur_name:
        return None
    
    jur_lower = jur_name.lower().strip()
    
    # Direct matches
    for jur in Jurisdiction:
        if (jur.value.lower() == jur_lower or 
            jur.name.upper() == jur_lower.upper() or
            jur_lower in jur.value.lower() or
            jur.value.lower() in jur_lower):
            return jur
    
    # Common variations
    if 'uae' in jur_lower or 'united arab emirates' in jur_lower:
        return Jurisdiction.UAE
    elif 'kuwait' in jur_lower:
        return Jurisdiction.KUWAIT
    elif 'qatar' in jur_lower:
        return Jurisdiction.QATAR
    elif 'oman' in jur_lower:
        return Jurisdiction.OMAN
    elif 'ksa' in jur_lower or 'saudi' in jur_lower or 'kingdom of saudi arabia' in jur_lower:
        return Jurisdiction.KSA
    elif 'difc' in jur_lower or 'dubai international financial centre' in jur_lower:
        return Jurisdiction.DIFC
    
    return None


# Backward compatibility functions
def detect_all_disclaimers_from_pdf(pdf_bytes: bytes, jurisdiction: Optional[Jurisdiction] = None) -> List[DetectedDisclaimer]:
    """
    Backward compatibility wrapper.
    """
    _, disclaimers = detect_jurisdictions_and_disclaimers(pdf_bytes)
    return disclaimers


def detect_disclaimer_from_pdf(pdf_bytes: bytes, jurisdiction: Optional[Jurisdiction] = None) -> Optional[DetectedDisclaimer]:
    """
    Backward compatibility wrapper.
    """
    _, disclaimers = detect_jurisdictions_and_disclaimers(pdf_bytes)
    if disclaimers:
        return disclaimers[0]
    return None
