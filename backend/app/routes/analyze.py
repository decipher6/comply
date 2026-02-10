# app/routes/analyze.py
from fastapi import APIRouter, UploadFile, File, HTTPException, Response
from fastapi.responses import StreamingResponse
from app.models import AnalysisResponse, Jurisdiction
from app.services.detect import detect_jurisdictions_and_disclaimers
from app.services.compare import compare_with_approved
from app.services.report import generate_analysis_result
from app.services.annotate import process_pdf_page_by_page
from app.database import analyses_collection
from datetime import datetime
from bson import ObjectId
from typing import Optional
import io
import base64

router = APIRouter(prefix="/api/analyze", tags=["analysis"])


@router.post("/", response_model=AnalysisResponse)
async def analyze_pdf(
    file: UploadFile = File(...),
    jurisdiction: Optional[Jurisdiction] = None
):
    """
    Analyze a PDF file for disclaimer compliance.
    
    Args:
        file: PDF file to analyze
        jurisdiction: Optional jurisdiction hint
        
    Returns:
        AnalysisResponse with compliance analysis
    """
    # Validate file type
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")
    
    try:
        # Read PDF file
        pdf_bytes = await file.read()
        
        if len(pdf_bytes) == 0:
            raise HTTPException(status_code=400, detail="PDF file is empty")
        
        # Detect jurisdictions and extract disclaimers for each
        jurisdictions_detected, all_detected = detect_jurisdictions_and_disclaimers(pdf_bytes)
        
        # Use first disclaimer as primary for backward compatibility
        primary_detected = all_detected[0] if all_detected else None
        
        # Compare with approved disclaimers for primary disclaimer
        comparison_results = []
        if primary_detected:
            comparison_results = compare_with_approved(primary_detected, jurisdiction)
        
        # Generate analysis result (applies relevant checklist to each jurisdiction and entire document)
        analysis_result = generate_analysis_result(
            primary_detected,
            all_detected,
            jurisdictions_detected,
            comparison_results,
            jurisdiction.value if jurisdiction else None,
            pdf_bytes  # Pass PDF bytes for entire document checking
        )
        
        # Generate annotated PDF and collect comments
        annotated_pdf_bytes, comments = process_pdf_page_by_page(
            pdf_bytes,
            analysis_result,
            all_detected
        )
        annotated_pdf_base64 = base64.b64encode(annotated_pdf_bytes).decode('utf-8')
        
        # Store analysis in database
        analysis_doc = {
            "timestamp": datetime.utcnow(),
            "filename": file.filename,
            "jurisdiction": jurisdiction.value if jurisdiction else None,
            "result": analysis_result.dict(),
            "detected_disclaimer": primary_detected.dict() if primary_detected else None,
            "all_detected_disclaimers": [d.dict() for d in all_detected],
            "jurisdictions_detected": jurisdictions_detected
        }
        
        inserted = analyses_collection.insert_one(analysis_doc)
        analysis_id = str(inserted.inserted_id)
        
        return AnalysisResponse(
            analysis_id=analysis_id,
            result=analysis_result,
            timestamp=datetime.utcnow(),
            annotated_pdf_base64=annotated_pdf_base64,
            comments=comments
        )
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(analysis_id: str):
    """
    Retrieve a previous analysis by ID.
    
    Args:
        analysis_id: Analysis ID
        
    Returns:
        AnalysisResponse
    """
    try:
        analysis_doc = analyses_collection.find_one({"_id": ObjectId(analysis_id)})
        if not analysis_doc:
            raise HTTPException(status_code=404, detail="Analysis not found")
        
        from app.models import AnalysisResult
        result = AnalysisResult(**analysis_doc["result"])
        
        return AnalysisResponse(
            analysis_id=analysis_id,
            result=result,
            timestamp=analysis_doc["timestamp"]
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid analysis ID: {str(e)}")


