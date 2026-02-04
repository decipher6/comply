# app/routes/approved.py
from fastapi import APIRouter, HTTPException
from app.models import ApprovedDisclaimer, Jurisdiction
from app.database import approved_collection
from datetime import datetime
from bson import ObjectId
from typing import List, Optional

router = APIRouter(prefix="/api/approved", tags=["approved"])


@router.post("/", response_model=ApprovedDisclaimer)
async def create_approved_disclaimer(disclaimer: ApprovedDisclaimer):
    """
    Create a new approved disclaimer.
    
    Args:
        disclaimer: ApprovedDisclaimer object
        
    Returns:
        Created ApprovedDisclaimer with ID
    """
    disclaimer_dict = disclaimer.dict(exclude={"id"})
    disclaimer_dict["created_at"] = datetime.utcnow()
    
    inserted = approved_collection.insert_one(disclaimer_dict)
    disclaimer_dict["id"] = str(inserted.inserted_id)
    
    return ApprovedDisclaimer(**disclaimer_dict)


@router.get("/", response_model=List[ApprovedDisclaimer])
async def list_approved_disclaimers(jurisdiction: Optional[Jurisdiction] = None):
    """
    List all approved disclaimers, optionally filtered by jurisdiction.
    
    Args:
        jurisdiction: Optional jurisdiction filter
        
    Returns:
        List of ApprovedDisclaimer objects
    """
    query = {}
    if jurisdiction:
        query["jurisdiction"] = jurisdiction.value
    
    disclaimers = []
    for doc in approved_collection.find(query):
        doc["id"] = str(doc["_id"])
        disclaimers.append(ApprovedDisclaimer(**doc))
    
    return disclaimers


@router.get("/{disclaimer_id}", response_model=ApprovedDisclaimer)
async def get_approved_disclaimer(disclaimer_id: str):
    """
    Get a specific approved disclaimer by ID.
    
    Args:
        disclaimer_id: Disclaimer ID
        
    Returns:
        ApprovedDisclaimer object
    """
    try:
        doc = approved_collection.find_one({"_id": ObjectId(disclaimer_id)})
        if not doc:
            raise HTTPException(status_code=404, detail="Disclaimer not found")
        
        doc["id"] = str(doc["_id"])
        return ApprovedDisclaimer(**doc)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid disclaimer ID: {str(e)}")


@router.put("/{disclaimer_id}", response_model=ApprovedDisclaimer)
async def update_approved_disclaimer(
    disclaimer_id: str,
    disclaimer: ApprovedDisclaimer
):
    """
    Update an existing approved disclaimer.
    
    Args:
        disclaimer_id: Disclaimer ID
        disclaimer: Updated ApprovedDisclaimer object
        
    Returns:
        Updated ApprovedDisclaimer
    """
    try:
        update_dict = disclaimer.dict(exclude={"id"})
        result = approved_collection.update_one(
            {"_id": ObjectId(disclaimer_id)},
            {"$set": update_dict}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Disclaimer not found")
        
        doc = approved_collection.find_one({"_id": ObjectId(disclaimer_id)})
        doc["id"] = str(doc["_id"])
        return ApprovedDisclaimer(**doc)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid disclaimer ID: {str(e)}")


@router.delete("/{disclaimer_id}")
async def delete_approved_disclaimer(disclaimer_id: str):
    """
    Delete an approved disclaimer.
    
    Args:
        disclaimer_id: Disclaimer ID
        
    Returns:
        Success message
    """
    try:
        result = approved_collection.delete_one({"_id": ObjectId(disclaimer_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Disclaimer not found")
        
        return {"message": "Disclaimer deleted successfully"}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"Invalid disclaimer ID: {str(e)}")
