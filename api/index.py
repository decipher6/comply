# Vercel serverless: all requests are routed here by vercel.json.
# Add backend to path so "app" package resolves to backend/app.
import sys
from pathlib import Path

_backend = Path(__file__).resolve().parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from app.main import app
