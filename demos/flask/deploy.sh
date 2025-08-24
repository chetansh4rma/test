#!/bin/bash

# Deployment script for fixed FHIR Flask App

echo "🚀 Deploying Fixed FHIR Flask App..."

# Install dependencies
echo "📦 Installing dependencies..."
pip install -r requirements.txt

# Set environment variables (adjust these for your production environment)
export FLASK_ENV=production
export SECRET_KEY="your-super-secret-production-key-here-change-this"
export MONGODB_URI="mongodb+srv://chetansharma9878600494:VibJosueveTfLF5V@cluster0.te5mtud.mongodb.net/"
export ALLOWED_ORIGINS="https://yourdomain.com,https://epic.com,https://fhir.epic.com"
export CLIENT_REDIRECT_URL="https://yourdomain.com/fhir"

# Test MongoDB connection
echo "🔍 Testing MongoDB connection..."
python -c "
from pymongo import MongoClient
try:
    client = MongoClient('$MONGODB_URI', serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    print('✅ MongoDB connection successful')
except Exception as e:
    print(f'❌ MongoDB connection failed: {e}')
    exit(1)
"

# Deploy with Gunicorn
echo "🚀 Starting Gunicorn server..."
gunicorn --config gunicorn.conf.py flask_app:app

echo "✅ Deployment complete!"
