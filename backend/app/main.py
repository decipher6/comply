# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import analyze, approved

app = FastAPI(
    title="Marketing Disclaimer Checker API",
    description="API for analyzing marketing PDFs for disclaimer compliance",
    version="1.0.0"
)

# CORS middleware (for future frontend integration)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(analyze.router)
app.include_router(approved.router)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Marketing Disclaimer Checker API",
        "version": "1.0.0",
        "endpoints": {
            "analyze": "/api/analyze/",
            "approved": "/api/approved/"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint. Returns 200 so Vercel/deploy checks pass; DB status in body."""
    from app.database import client
    try:
        client.admin.command('ping')
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        # Return 200 so load balancers don't treat as 500; details in body
        return {"status": "unhealthy", "database": "disconnected", "error": (str(e)[:200] or "unknown")}
