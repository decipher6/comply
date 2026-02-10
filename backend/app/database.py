# app/database.py
from pymongo import MongoClient
from app.config import settings

# Defer ping to avoid failing app load on Vercel when DB is unreachable at cold start
try:
    client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
except Exception as e:
    print(f"Warning: MongoClient init: {e}")
    client = MongoClient(settings.MONGO_URI)

db = client[settings.DB_NAME]

approved_collection = db["approved_disclaimers"]
analyses_collection = db["analyses"]
