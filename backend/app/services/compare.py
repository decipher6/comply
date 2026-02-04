# app/services/compare.py
from app.models import DetectedDisclaimer, ApprovedDisclaimer, ComparisonResult, Jurisdiction
from app.database import approved_collection
from typing import List, Optional
import re


def calculate_similarity(text1: str, text2: str) -> float:
    """
    Calculate text similarity using word overlap (Jaccard similarity).
    Simple and lightweight - no ML models needed.
    
    Args:
        text1: First text
        text2: Second text
        
    Returns:
        Similarity score between 0 and 1
    """
    # Normalize texts
    def normalize_text(text):
        # Remove punctuation and convert to lowercase
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        # Split into words and filter out short words
        words = [w for w in text.split() if len(w) > 2]
        return set(words)
    
    words1 = normalize_text(text1)
    words2 = normalize_text(text2)
    
    if not words1 or not words2:
        return 0.0
    
    # Calculate Jaccard similarity (intersection over union)
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    
    if union == 0:
        return 0.0
    
    similarity = intersection / union
    
    # Boost similarity if there are many common words (longer texts)
    if intersection > 5:
        similarity = min(1.0, similarity * 1.1)
    
    return float(similarity)


def check_phrase_presence(text: str, phrase: str) -> bool:
    """
    Check if a required phrase is present in the text (case-insensitive, flexible matching).
    
    Args:
        text: Text to search in
        phrase: Phrase to find
        
    Returns:
        True if phrase is found
    """
    # Normalize both texts
    text_normalized = re.sub(r'\s+', ' ', text.lower().strip())
    phrase_normalized = re.sub(r'\s+', ' ', phrase.lower().strip())
    
    # Direct substring match
    if phrase_normalized in text_normalized:
        return True
    
    # Check if all significant words are present
    phrase_words = [w for w in phrase_normalized.split() if len(w) > 3]
    if len(phrase_words) == 0:
        return False
    
    text_words = set(text_normalized.split())
    phrase_words_set = set(phrase_words)
    
    # If most significant words are present, consider it a match
    return len(phrase_words_set.intersection(text_words)) >= len(phrase_words_set) * 0.7


def compare_with_approved(
    detected: DetectedDisclaimer,
    jurisdiction: Optional[Jurisdiction] = None
) -> List[ComparisonResult]:
    """
    Compare detected disclaimer against approved disclaimers in the database.
    
    Args:
        detected: Detected disclaimer from the document
        jurisdiction: Optional jurisdiction filter
        
    Returns:
        List of comparison results sorted by similarity
    """
    # Build query
    query = {}
    if jurisdiction:
        query["jurisdiction"] = jurisdiction.value
    elif detected.jurisdiction:
        query["jurisdiction"] = detected.jurisdiction.value
    
    # Fetch approved disclaimers
    approved_list = list(approved_collection.find(query))
    
    if not approved_list:
        # If no jurisdiction match, try all disclaimers
        approved_list = list(approved_collection.find({}))
    
    if not approved_list:
        return []
    
    comparison_results = []
    
    for approved_doc in approved_list:
        approved = ApprovedDisclaimer(**approved_doc)
        
        # Calculate semantic similarity
        similarity = calculate_similarity(detected.text, approved.full_text)
        
        # Check required phrases
        matched_phrases = []
        missing_phrases = []
        
        for phrase in approved.required_phrases:
            if check_phrase_presence(detected.text, phrase):
                matched_phrases.append(phrase)
            else:
                missing_phrases.append(phrase)
        
        comparison_results.append(ComparisonResult(
            approved_disclaimer_id=str(approved.id),
            similarity_score=similarity,
            matched_phrases=matched_phrases,
            missing_phrases=missing_phrases
        ))
    
    # Sort by similarity score (descending)
    comparison_results.sort(key=lambda x: x.similarity_score, reverse=True)
    
    return comparison_results
