# app/models.py
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from enum import Enum
from datetime import datetime


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Jurisdiction(str, Enum):
    OMAN = "Oman"
    QATAR = "Qatar"
    DIFC = "DIFC"
    KSA = "KSA"
    UAE = "UAE"
    KUWAIT = "Kuwait"


class ApprovedDisclaimer(BaseModel):
    id: Optional[str] = None
    category: str
    jurisdiction: Jurisdiction
    full_text: str
    required_phrases: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class DetectedDisclaimer(BaseModel):
    text: str
    jurisdiction: Optional[Jurisdiction] = None
    confidence: Optional[float] = None


class DetectedDisclaimers(BaseModel):
    """Container for all detected disclaimers across jurisdictions."""
    disclaimers: List[DetectedDisclaimer] = Field(default_factory=list)
    primary: Optional[DetectedDisclaimer] = None  # Primary/main disclaimer


class MissingPhrase(BaseModel):
    phrase: str
    required: bool
    reason: Optional[str] = None
    exact_highlight_text: Optional[str] = None  # Exact text from document to highlight (where to add or related line)


class ChecklistItem(BaseModel):
    """A single checklist item with its compliance status."""
    item: str
    section: str  # e.g., "GENERAL REQUIREMENTS", "UAE_SCA_REQUIREMENTS"
    is_required: bool  # Marked with * in checklist
    is_compliant: bool  # Whether this item is met
    missing_details: Optional[str] = None  # Details if not compliant
    exact_highlight_text: Optional[str] = None  # Exact quote from document to highlight for this item


class ViolationDetail(BaseModel):
    """A violation with optional exact text to highlight in the PDF."""
    violation: str
    exact_text: Optional[str] = None  # Exact quote from document that violates (for highlighting)


class ComparisonResult(BaseModel):
    approved_disclaimer_id: str
    similarity_score: float
    matched_phrases: List[str] = Field(default_factory=list)
    missing_phrases: List[str] = Field(default_factory=list)


class ChecklistResult(BaseModel):
    """Checklist results for a specific jurisdiction."""
    jurisdiction: Optional[Jurisdiction] = None
    checklist_items: List[ChecklistItem] = Field(default_factory=list)
    missing_required: List[MissingPhrase] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list)  # Kept for backward compat
    violation_details: List[ViolationDetail] = Field(default_factory=list)  # Violation + exact text to highlight


class FootnoteIssue(BaseModel):
    """A footnote reference or formatting issue."""
    page: int
    issue_type: str  # e.g. footnote_reference_missing
    message: str
    reference: Optional[str] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1] for highlighting the problematic ref or footnote text


class FormattingIssue(BaseModel):
    """Unusual colored text (red edit remnants) or existing highlight in the PDF."""
    page: int
    issue_type: str  # unusual_color | existing_highlight
    message: str
    text: Optional[str] = None
    color_hex: Optional[str] = None
    bbox: Optional[List[float]] = None  # [x0, y0, x1, y1] for highlighting red text


class AnalysisResult(BaseModel):
    is_approved: bool
    risk_level: RiskLevel
    detected_disclaimer: Optional[DetectedDisclaimer] = None  # Primary disclaimer (for backward compatibility)
    all_detected_disclaimers: List[DetectedDisclaimer] = Field(default_factory=list)  # All disclaimers found
    jurisdictions_detected: List[str] = Field(default_factory=list)  # List of jurisdictions found in document
    comparison_results: List[ComparisonResult] = Field(default_factory=list)
    missing_required_phrases: List[MissingPhrase] = Field(default_factory=list)
    checklist_results: List[ChecklistResult] = Field(default_factory=list)  # Checklist results per jurisdiction
    closest_match_id: Optional[str] = None
    explanation: str
    llm_suggestions: Optional[str] = None
    summary_blurb: Optional[str] = None  # Short 1–2 sentence summary for UI (why OK / why not)
    # Universal footnotes for the entire document (one section, or none): { "1": "text", "2": "text" }
    footnotes: Optional[Dict[str, str]] = None
    # ref -> {"page": int, "bbox": [x0,y0,x1,y1]} for highlighting footnote definitions that have issues
    footnotes_locations: Optional[Dict[str, Dict]] = None
    footnote_issues: List[FootnoteIssue] = Field(default_factory=list)
    formatting_issues: List[FormattingIssue] = Field(default_factory=list)  # Unusual color, existing highlights


class AnalysisRequest(BaseModel):
    jurisdiction: Optional[Jurisdiction] = None


class AnalysisResponse(BaseModel):
    analysis_id: str
    result: AnalysisResult
    timestamp: datetime
    annotated_pdf_base64: Optional[str] = None  # Base64 encoded annotated PDF
    comments: List[dict] = Field(default_factory=list)  # Comments for side panel