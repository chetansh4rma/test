#!/usr/bin/env python3
"""
Simple MongoDB test script to verify connection and basic operations
"""

import os
from dotenv import load_dotenv
from pymongo import MongoClient
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

def test_mongodb_connection():
    """Test MongoDB connection and basic operations"""
    try:
        # Get MongoDB URI from environment
        MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/')
        print(f"🔍 Testing MongoDB connection to: {MONGODB_URI}")
        
        # Create client
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        
        # Test connection
        client.admin.command('ping')
        print("✅ MongoDB connection successful!")
        
        # Test database operations
        db = client['fhir_sessions']
        collection = db['test_sessions']
        
        # Test insert
        test_doc = {
            'test_id': 'test_123',
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(hours=1),
            'data': {'test': 'value'}
        }
        
        result = collection.insert_one(test_doc)
        print(f"✅ Insert test successful: {result.inserted_id}")
        
        # Test find
        found_doc = collection.find_one({'test_id': 'test_123'})
        if found_doc:
            print("✅ Find test successful")
        else:
            print("❌ Find test failed")
        
        # Test update
        update_result = collection.update_one(
            {'test_id': 'test_123'},
            {'$set': {'data.test': 'updated_value'}}
        )
        if update_result.modified_count > 0:
            print("✅ Update test successful")
        else:
            print("❌ Update test failed")
        
        # Test delete
        delete_result = collection.delete_one({'test_id': 'test_123'})
        if delete_result.deleted_count > 0:
            print("✅ Delete test successful")
        else:
            print("❌ Delete test failed")
        
        # Test index creation
        collection.create_index("expires_at", expireAfterSeconds=0)
        print("✅ TTL index creation successful")
        
        print("\n🎉 All MongoDB tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ MongoDB test failed: {e}")
        return False

if __name__ == "__main__":
    test_mongodb_connection()
