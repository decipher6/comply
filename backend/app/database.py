# app/database.py
from pymongo import MongoClient
from app.config import settings

try:
    client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
    # Test connection
    client.admin.command('ping')
except Exception as e:
    print(f"Warning: Could not connect to MongoDB: {e}")
    print("The application will start but database operations may fail.")
    client = MongoClient(settings.MONGO_URI)

db = client[settings.DB_NAME]

approved_collection = db["approved_disclaimers"]
analyses_collection = db["analyses"]
