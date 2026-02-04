# app/services/rules.py
from app.models import RiskLevel, ComparisonResult, DetectedDisclaimer, MissingPhrase
from typing import List, Optional


def classify_risk_level(
    missing_phrases: List[MissingPhrase],
    checklist_violations: List[str] = None
) -> RiskLevel:
    """
    Classify risk level based ONLY on checklist compliance.
    
    Rule-based logic based on checklist:
    - HIGH: Critical required elements missing (marked with * in checklist) OR 3+ missing requirements
    - MEDIUM: Some required elements missing (1-2 missing requirements)
    - LOW: All required elements present (no missing requirements)
    
    Args:
        missing_phrases: List of missing required phrases/elements
        checklist_violations: List of checklist violations found
        
    Returns:
        RiskLevel enum value
    """
    if checklist_violations is None:
        checklist_violations = []
    
    # Count critical missing elements (required statements marked with *)
    critical_missing = len([p for p in missing_phrases if p.required])
    total_violations = critical_missing + len(checklist_violations)
    
    # HIGH risk: 3+ missing requirements or critical violations
    if total_violations >= 3 or checklist_violations:
        return RiskLevel.HIGH
    
    # MEDIUM risk: 1-2 missing requirements
    if total_violations >= 1:
        return RiskLevel.MEDIUM
    
    # LOW risk: All requirements met
    return RiskLevel.LOW


def determine_approval_status(
    risk_level: RiskLevel,
    missing_phrases: List[MissingPhrase],
    checklist_violations: List[str] = None
) -> bool:
    """
    Determine if the disclaimer is approved based ONLY on checklist compliance.
    
    Args:
        risk_level: Classified risk level
        missing_phrases: List of missing phrases
        checklist_violations: List of checklist violations
        
    Returns:
        True if approved (LOW risk, no missing requirements), False otherwise
    """
    if checklist_violations is None:
        checklist_violations = []
    
    # Only approve if risk is LOW and no missing requirements
    if risk_level == RiskLevel.LOW:
        return len(missing_phrases) == 0 and len(checklist_violations) == 0
    
    return False
