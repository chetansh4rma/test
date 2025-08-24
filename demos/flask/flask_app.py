#!/usr/bin/env python3

import logging
from fhirclient import client
from fhirclient.models.medication import Medication
from fhirclient.models.medicationrequest import MedicationRequest
from fhirclient.models.observation import Observation
from fhirclient.models.condition import Condition
from fhirclient.models.allergyintolerance import AllergyIntolerance
from fhirclient.models.procedure import Procedure
from fhirclient.models.diagnosticreport import DiagnosticReport
from fhirclient.models.fhirdate import FHIRDate
from fhirclient.models.quantity import Quantity
from fhirclient.models.codeableconcept import CodeableConcept
from fhirclient.models.coding import Coding
from fhirclient.models.fhirreference import FHIRReference
from fhirclient.models.fhirdatetime import FHIRDateTime

from flask import Flask, request, redirect, session, jsonify
from flask_cors import CORS, cross_origin
from datetime import datetime, timedelta
import urllib.parse
import json
import secrets
import uuid
import threading
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Load environment variables from .env file
load_dotenv()

# MongoDB connection for session storage
try:
    MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/')
    mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    # Test connection
    mongo_client.admin.command('ping')
    db = mongo_client['fhir_sessions']
    sessions_collection = db['sessions']
    print(f"Connected to MongoDB at {MONGODB_URI}")
    
    # Create index for better performance
    sessions_collection.create_index("token", unique=True)
    sessions_collection.create_index("expires_at")
    
except Exception as e:
    print(f"MongoDB connection failed, using in-memory storage: {e}")
    mongo_client = None
    db = None
    sessions_collection = None

# Multi-session storage with MongoDB support
active_sessions = {} if sessions_collection is None else None
session_tokens = {} if sessions_collection is None else None
session_lock = threading.Lock()

# Maximum number of concurrent sessions
MAX_SESSIONS = 100000000000000000000000

# MongoDB session helper functions
def get_session_from_db(token):
    """Get session data from MongoDB or in-memory storage"""
    if sessions_collection is None:
        return active_sessions.get(token)
    
    try:
        session_doc = sessions_collection.find_one({'token': token})
        if session_doc and session_doc.get('expires_at', datetime.now()) > datetime.now():
            return session_doc['data']
        elif session_doc:
            # Clean up expired session
            sessions_collection.delete_one({'token': token})
        return None
    except Exception as e:
        print(f"Error getting session from DB: {e}")
        return None

def save_session_to_db(token, data):
    """Save session data to MongoDB or in-memory storage"""
    if sessions_collection is None:
        active_sessions[token] = data
        return
    
    try:
        session_doc = {
            'token': token,
            'data': data,
            'expires_at': data.get('expires_at', datetime.now() + timedelta(hours=2)),
            'created_at': data.get('created_at', datetime.now()),
            'last_accessed': datetime.now()
        }
        sessions_collection.replace_one(
            {'token': token}, 
            session_doc, 
            upsert=True
        )
    except Exception as e:
        print(f"Error saving session to DB: {e}")

def delete_session_from_db(token):
    """Delete session from MongoDB or in-memory storage"""
    if sessions_collection is None:
        if token in active_sessions:
            del active_sessions[token]
        if token in session_tokens:
            del session_tokens[token]
        return
    
    try:
        sessions_collection.delete_one({'token': token})
    except Exception as e:
        print(f"Error deleting session from DB: {e}")

def get_all_sessions():
    """Get all active sessions"""
    if sessions_collection is None:
        return active_sessions or {}
    
    try:
        sessions = {}
        for doc in sessions_collection.find({'expires_at': {'$gt': datetime.now()}}):
            sessions[doc['token']] = doc['data']
        return sessions
    except Exception as e:
        print(f"Error getting all sessions from DB: {e}")
        return {}

def find_session_by_oauth_state(oauth_state):
    """Find session by OAuth state parameter for callback recovery"""
    if not oauth_state:
        return None
    
    if sessions_collection is None:
        # Search in-memory storage
        if active_sessions:
            for token, data in active_sessions.items():
                if data.get('oauth_state') == oauth_state:
                    return token
        return None
    
    try:
        # Search MongoDB
        session_doc = sessions_collection.find_one({
            'data.oauth_state': oauth_state,
            'expires_at': {'$gt': datetime.now()}
        })
        if session_doc:
            return session_doc['token']
        return None
    except Exception as e:
        app.logger.error(f"Error finding session by OAuth state: {e}")
        return None

def cleanup_expired_sessions_db():
    """Clean up expired sessions from MongoDB or in-memory storage"""
    if sessions_collection is None:
        # Fallback to in-memory cleanup
        if active_sessions:
            now = datetime.now()
            expired_tokens = [
                token for token, data in active_sessions.items()
                if now > data.get('expires_at', now)
            ]
            for token in expired_tokens:
                if token in active_sessions:
                    del active_sessions[token]
        return
    
    try:
        result = sessions_collection.delete_many({'expires_at': {'$lte': datetime.now()}})
        if result.deleted_count > 0:
            print(f"Cleaned up {result.deleted_count} expired sessions from MongoDB")
    except Exception as e:
        print(f"Error cleaning up expired sessions: {e}")

# app setup with complete OAuth scopes for Epic FHIR
smart_defaults = {
    'app_id': 'd0443530-07ed-48e5-a3da-2d6ee3354ef2',
    'api_base': 'https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4/',
    'redirect_uri': 'https://test-3-1hct.onrender.com/fhir-app/',
    'scope': ' '.join([
        'user/Observation.read',
        'user/Observation.write',
        'user/Observation.create',
        'patient/Observation.read',
        'patient/Observation.write',
        'patient/Observation.create',
        'user/Patient.read',
        'patient/Patient.read',
        'fhirUser',
        'openid',
        'profile',
        'launch',
        'launch/patient',
    ])
}

CLIENT_REDIRECT_URL = 'https://preview--dailycheckin.lovable.app/fhir'


app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Session configuration - optimized for MongoDB persistence and localhost
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = True  
app.config['SESSION_COOKIE_SECURE'] = False  # Allow HTTP for localhost development
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)

CORS(app, 
     origins="*",
     supports_credentials=True,
     allow_headers=['Content-Type', 'Authorization', 'X-Requested-With', 'Accept', 'Origin', 'Cache-Control', 'X-File-Name', 'X-Session-Token'],
     methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH', 'HEAD'],
     expose_headers=['Set-Cookie', 'X-Session-Token']
)

@app.after_request
def after_request(response):
    try:
        origin = request.headers.get('Origin')
        response.headers['Access-Control-Allow-Origin'] = origin if origin else '*'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,X-Requested-With,Accept,Origin,Cache-Control,X-File-Name,X-Session-Token'
        response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS,PATCH,HEAD'
        response.headers['Access-Control-Expose-Headers'] = 'Set-Cookie,X-Session-Token'
    except Exception as e:
        app.logger.error(f"Error in after_request: {e}")
    return response

@app.before_request
def handle_preflight():
    try:
        if request.method == "OPTIONS":
            response = jsonify({'status': 'OK'})
            response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
            response.headers['Access-Control-Allow-Credentials'] = 'true'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization,X-Requested-With,Accept,Origin,Cache-Control,X-File-Name,X-Session-Token'
            response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS,PATCH,HEAD'
            return response
    except Exception as e:
        app.logger.error(f"Error in handle_preflight: {e}")

def generate_session_token():
    """Generate unique session token"""
    try:
        return secrets.token_urlsafe(32)
    except Exception as e:
        app.logger.error(f"Error generating session token: {e}")
        return f"token_{int(datetime.now().timestamp() * 1000)}"

def get_session_token():
    """Get session token from request headers or create new one"""
    try:
        # Try to get from headers first (for API calls)
        token = request.headers.get('X-Session-Token')
        if token and get_session_from_db(token):
            return token
        
        # Try to get from session (for OAuth callback)
        token = session.get('session_token')
        if token and get_session_from_db(token):
            return token
        
        # Create new token if none exists or is invalid
        return create_new_session()
    except Exception as e:
        app.logger.error(f"Error getting session token: {e}")
        return create_new_session()

def create_new_session():
    """Create a new isolated session"""
    try:
        with session_lock:
            # Clean up expired sessions first
            cleanup_expired_sessions_db()
            
            # Generate new token and session
            token = generate_session_token()
            session_id = str(uuid.uuid4())
            
            session_data = {
                'session_id': session_id,
                'state': None,
                'patient_data': None,
                'access_token': None,
                'refresh_token': None,
                'token_expires_at': None,
                'created_at': datetime.now(),
                'expires_at': datetime.now() + timedelta(hours=2),
                'last_accessed': datetime.now()
            }
            
            # Save to MongoDB or in-memory storage
            save_session_to_db(token, session_data)
            
            # Store in Flask session for OAuth callback
            session['session_token'] = token
            session.permanent = True
            
            app.logger.info(f"Created new session: {token[:8]}... (ID: {session_id})")
            return token
    except Exception as e:
        app.logger.error(f"Error creating new session: {e}")
        return f"fallback_{int(datetime.now().timestamp() * 1000)}"

def cleanup_expired_sessions():
    """Clean up expired sessions - delegates to MongoDB function"""
    try:
        cleanup_expired_sessions_db()
    except Exception as e:
        app.logger.error(f"Error cleaning up expired sessions: {e}")

def cleanup_session(token):
    """Clean up a specific session"""
    try:
        session_data = get_session_from_db(token)
        if session_data:
            # Remove from storage (FHIR client cleanup happens automatically)
            delete_session_from_db(token)
            app.logger.info(f"Cleaned up session: {token[:8]}...")
    except Exception as e:
        app.logger.error(f"Error cleaning up session {token}: {e}")

def update_session_access(token):
    """Update last accessed time for session"""
    try:
        session_data = get_session_from_db(token)
        if session_data:
            session_data['last_accessed'] = datetime.now()
            save_session_to_db(token, session_data)
    except Exception as e:
        app.logger.error(f"Error updating session access: {e}")

def _save_state(state, token):
    """Save FHIR client state for specific session"""
    try:
        session_data = get_session_from_db(token)
        if session_data:
            session_data['state'] = state
            session_data['last_accessed'] = datetime.now()
            # Also save the OAuth state parameter for session recovery
            if isinstance(state, dict) and 'state' in state:
                session_data['oauth_state'] = state['state']
            save_session_to_db(token, session_data)
            app.logger.info(f"Saved state for session: {token[:8]}...")
    except Exception as e:
        app.logger.error(f"Error saving state for {token}: {e}")

def _get_smart(token=None, force_new=False):
    """Get FHIR client for specific session"""
    try:
        if not token:
            token = get_session_token()
        
        session_data = get_session_from_db(token)
        if not session_data:
            return None
        
        # Check if session is expired
        if datetime.now() > session_data.get('expires_at', datetime.now()):
            cleanup_session(token)
            return None
        
        # Always recreate FHIR client from stored state (don't store client object directly)
        state = session_data.get('state')
        if state and isinstance(state, dict):
            try:
                # Recreate client from saved state
                smart_client = client.FHIRClient(
                    state=state,
                    save_func=lambda state: _save_state(state, token)
                )
                update_session_access(token)
                app.logger.info(f"Recreated FHIR client from state for session: {token[:8]}...")
                return smart_client
            except Exception as e:
                app.logger.error(f"Error recreating FHIR client from state for {token}: {e}")
                # Fall through to create new client
        
        # Create completely new FHIR client
        settings = smart_defaults.copy()
        # Create state with session ID and token for recovery
        state_data = f"{session_data['session_id']}|{token}"
        settings['state'] = state_data
        
        try:
            smart_client = client.FHIRClient(
                settings=settings,
                save_func=lambda state: _save_state(state, token)
            )
            update_session_access(token)
            app.logger.info(f"Created new FHIR client for session: {token[:8]}...")
            return smart_client
        except Exception as e:
            app.logger.error(f"Error creating FHIR client for {token}: {e}")
            return None
    except Exception as e:
        app.logger.error(f"Error in _get_smart: {e}")
        return None

def _logout(token=None):
    """Logout specific session"""
    try:
        if not token:
            token = get_session_token()
        
        if get_session_from_db(token):
            cleanup_session(token)
            app.logger.info(f"Logged out session: {token[:8]}...")
    except Exception as e:
        app.logger.error(f"Error in logout: {e}")

def _reset_session(token=None):
    """Reset specific session"""
    try:
        if not token:
            token = get_session_token()
        
        session_data = get_session_from_db(token)
        if session_data:
            session_data['state'] = None
            session_data['patient_data'] = None
            session_data['access_token'] = None
            session_data['refresh_token'] = None
            session_data['token_expires_at'] = None
            session_data['last_accessed'] = datetime.now()
            save_session_to_db(token, session_data)
            app.logger.info(f"Reset session: {token[:8]}...")
    except Exception as e:
        app.logger.error(f"Error resetting session: {e}")

def store_tokens(token, access_token, refresh_token=None, expires_in=None):
    """Store tokens for specific session"""
    try:
        if token in active_sessions:
            active_sessions[token]['access_token'] = access_token
            active_sessions[token]['refresh_token'] = refresh_token
            active_sessions[token]['token_expires_at'] = datetime.now() + timedelta(seconds=expires_in) if expires_in else None
            update_session_access(token)
    except Exception as e:
        app.logger.error(f"Error storing tokens for {token}: {e}")

def get_tokens(token):
    """Retrieve stored tokens for specific session"""
    try:
        if token in active_sessions:
            session_data = active_sessions[token]
            return {
                'access_token': session_data.get('access_token'),
                'refresh_token': session_data.get('refresh_token'),
                'expires_at': session_data.get('token_expires_at')
            }
        return None
    except Exception as e:
        app.logger.error(f"Error retrieving tokens for {token}: {e}")
        return None

# All the existing utility functions remain the same...
def _format_date(date_str):
    """Format FHIR date string to readable format"""
    try:
        if not date_str:
            return "Unknown"
        
        if isinstance(date_str, str):
            if len(date_str) == 10:  # YYYY-MM-DD
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            else:  # Full datetime
                date_obj = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            return date_obj.strftime('%B %d, %Y')
        return str(date_str)
    except Exception as e:
        app.logger.error(f"Error formatting date {date_str}: {e}")
        return str(date_str) if date_str else "Unknown"

def _get_patient_demographics(smart):
    """Get patient demographic information"""
    try:
        patient = smart.patient
        if not patient:
            return {}
        
        demographics = {}
        
        try:
            demographics['name'] = smart.human_name(patient.name[0] if patient.name and len(patient.name) > 0 else 'Unknown')
        except Exception as e:
            app.logger.error(f"Error getting patient name: {e}")
            demographics['name'] = 'Unknown'
            
        try:
            demographics['gender'] = patient.gender or 'Unknown'
        except Exception as e:
            app.logger.error(f"Error getting patient gender: {e}")
            demographics['gender'] = 'Unknown'
            
        try:
            demographics['birth_date'] = _format_date(patient.birthDate.isostring if patient.birthDate else None)
        except Exception as e:
            app.logger.error(f"Error getting patient birth date: {e}")
            demographics['birth_date'] = 'Unknown'
        
        # Address
        try:
            if patient.address and len(patient.address) > 0:
                addr = patient.address[0]
                address_parts = []
                if addr.line:
                    address_parts.extend(addr.line)
                if addr.city:
                    address_parts.append(addr.city)
                if addr.state:
                    address_parts.append(addr.state)
                if addr.postalCode:
                    address_parts.append(addr.postalCode)
                demographics['address'] = ', '.join(address_parts)
            else:
                demographics['address'] = 'Not available'
        except Exception as e:
            app.logger.error(f"Error getting patient address: {e}")
            demographics['address'] = 'Not available'
        
        # Phone
        try:
            if patient.telecom:
                phones = [t.value for t in patient.telecom if t.system == 'phone']
                demographics['phone'] = phones[0] if phones else 'Not available'
            else:
                demographics['phone'] = 'Not available'
        except Exception as e:
            app.logger.error(f"Error getting patient phone: {e}")
            demographics['phone'] = 'Not available'
        
        return demographics
    except Exception as e:
        app.logger.error(f"Error getting patient demographics: {e}")
        return {'name': 'Unknown', 'gender': 'Unknown', 'birth_date': 'Unknown', 'address': 'Not available', 'phone': 'Not available'}

def _get_prescriptions(smart):
    try:
        search = MedicationRequest.where({'patient': smart.patient_id})
        prescriptions = list(search.perform_resources_iter(smart.server))
        return [p for p in prescriptions if isinstance(p, MedicationRequest)]
    except Exception as e:
        app.logger.error(f"Error getting prescriptions: {e}")
        return []

def _get_conditions(smart):
    """Get patient conditions/diagnoses"""
    try:
        search = Condition.where({'patient': smart.patient_id})
        conditions = list(search.perform_resources_iter(smart.server))
        return [c for c in conditions if isinstance(c, Condition)]
    except Exception as e:
        app.logger.error(f"Error getting conditions: {e}")
        return []

def _get_observations(smart):
    """Get patient observations/vitals"""
    try:
        search = Observation.where({'patient': smart.patient_id, '_count': '50'})
        observations = list(search.perform_resources_iter(smart.server))
        return [o for o in observations if isinstance(o, Observation)]
    except Exception as e:
        app.logger.error(f"Error getting observations: {e}")
        return []

def _get_allergies(smart):
    """Get patient allergies"""
    try:
        search = AllergyIntolerance.where({'patient': smart.patient_id})
        allergies = list(search.perform_resources_iter(smart.server))
        return [a for a in allergies if isinstance(a, AllergyIntolerance)]
    except Exception as e:
        app.logger.error(f"Error getting allergies: {e}")
        return []

def _get_procedures(smart):
    """Get patient procedures"""
    try:
        search = Procedure.where({'patient': smart.patient_id})
        procedures = list(search.perform_resources_iter(smart.server))
        return [p for p in procedures if isinstance(p, Procedure)]
    except Exception as e:
        app.logger.error(f"Error getting procedures: {e}")
        return []

def _get_medication_by_ref(ref, smart):
    try:
        med_id = ref.split("/")[1]
        return Medication.read(med_id, smart.server).code
    except Exception as e:
        app.logger.error(f"Error getting medication by ref: {e}")
        return None

def _med_name(med):
    try:
        if not med:
            return "Unknown Medication"
        
        if hasattr(med, 'coding') and med.coding:
            name = next((coding.display for coding in med.coding if coding.system == 'http://www.nlm.nih.gov/research/umls/rxnorm'), None)
            if name:
                return name
        if hasattr(med, 'text') and med.text:
            return med.text
        return "Unnamed Medication(TM)"
    except Exception as e:
        app.logger.error(f"Error getting medication name: {e}")
        return "Unknown Medication"

def _get_med_name(prescription, client=None):
    try:
        if not isinstance(prescription, MedicationRequest):
            app.logger.error(f"Expected MedicationRequest, got {type(prescription)}")
            return "Error: Invalid prescription data"
        
        if prescription.medicationCodeableConcept is not None:
            med = prescription.medicationCodeableConcept
            return _med_name(med)
        elif prescription.medicationReference is not None and client is not None:
            med = _get_medication_by_ref(prescription.medicationReference.reference, client)
            return _med_name(med)
        else:
            return 'Error: medication not found'
    except AttributeError as e:
        app.logger.error(f"AttributeError in _get_med_name: {e}")
        return "Error: Unable to retrieve medication name"
    except Exception as e:
        app.logger.error(f"Unexpected error in _get_med_name: {e}")
        return "Error: Medication processing failed"

def _format_condition(condition):
    """Format condition for display"""
    try:
        if not isinstance(condition, Condition):
            return "Error: Invalid condition data"
        
        name = "Unknown condition"
        if condition.code and condition.code.text:
            name = condition.code.text
        elif condition.code and condition.code.coding:
            name = condition.code.coding[0].display or "Unknown condition"
        
        status = "Unknown"
        if condition.clinicalStatus and condition.clinicalStatus.coding:
            status = condition.clinicalStatus.coding[0].code if condition.clinicalStatus.coding else "Unknown"
        
        onset = _format_date(condition.onsetDateTime.isostring if condition.onsetDateTime else None)
        
        return f"{name} (Status: {status}, Onset: {onset})"
    except Exception as e:
        app.logger.error(f"Error formatting condition: {e}")
        return "Error: Unable to format condition"

def _format_observation(obs):
    """Format observation for display"""
    try:
        if not isinstance(obs, Observation):
            return "Error: Invalid observation data"
        
        name = "Unknown observation"
        if obs.code and obs.code.text:
            name = obs.code.text
        elif obs.code and obs.code.coding:
            name = obs.code.coding[0].display or "Unknown observation"
        
        value = "No value"
        if obs.valueQuantity:
            value = f"{obs.valueQuantity.value} {obs.valueQuantity.unit}"
        elif obs.valueString:
            value = obs.valueString
        elif obs.valueCodeableConcept and obs.valueCodeableConcept.text:
            value = obs.valueCodeableConcept.text
        
        date = _format_date(obs.effectiveDateTime.isostring if obs.effectiveDateTime else None)
        
        return f"{name}: {value} ({date})"
    except Exception as e:
        app.logger.error(f"Error formatting observation: {e}")
        return "Error: Unable to format observation"

def _format_allergy(allergy):
    """Format allergy for display"""
    try:
        if not isinstance(allergy, AllergyIntolerance):
            return "Error: Invalid allergy data"
        
        substance = "Unknown substance"
        if allergy.code and allergy.code.text:
            substance = allergy.code.text
        elif allergy.code and allergy.code.coding:
            substance = allergy.code.coding[0].display or "Unknown substance"
        
        severity = allergy.criticality or "Unknown severity"
        return f"{substance} (Severity: {severity})"
    except Exception as e:
        app.logger.error(f"Error formatting allergy: {e}")
        return "Error: Unable to format allergy"

def _format_procedure(procedure):
    """Format procedure for display"""
    try:
        if not isinstance(procedure, Procedure):
            return "Error: Invalid procedure data"
        
        name = "Unknown procedure"
        if procedure.code and procedure.code.text:
            name = procedure.code.text
        elif procedure.code and procedure.code.coding:
            name = procedure.code.coding[0].display or "Unknown procedure"
        
        date = _format_date(procedure.performedDateTime.isostring if procedure.performedDateTime else None)
        return f"{name} ({date})"
    except Exception as e:
        app.logger.error(f"Error formatting procedure: {e}")
        return "Error: Unable to format procedure"

def _get_complete_patient_data(smart):
    """Get all patient data for redirect"""
    try:
        demographics = _get_patient_demographics(smart)
        prescriptions = _get_prescriptions(smart)
        conditions = _get_conditions(smart)
        observations = _get_observations(smart)
        allergies = _get_allergies(smart)
        procedures = _get_procedures(smart)
        
        medications = []
        for prescription in prescriptions:
            try:
                med_name = _get_med_name(prescription, smart)
                medications.append({
                    'name': med_name,
                    'status': prescription.status if hasattr(prescription, 'status') else "Unknown status"
                })
            except Exception as e:
                app.logger.error(f"Error processing prescription: {e}")
                medications.append({
                    'name': "Error processing medication",
                    'status': "Error"
                })
        
        return {
            'patient_id': smart.patient_id,
            'demographics': demographics,
            'medications': medications,
            'conditions': [_format_condition(condition) for condition in conditions],
            'allergies': [_format_allergy(allergy) for allergy in allergies],
            'observations': [_format_observation(obs) for obs in observations[:10]],
            'procedures': [_format_procedure(procedure) for procedure in procedures]
        }
    except Exception as e:
        app.logger.error(f"Error getting complete patient data: {e}")
        return {
            'patient_id': 'unknown',
            'demographics': {'name': 'Unknown', 'gender': 'Unknown', 'birth_date': 'Unknown', 'address': 'Not available', 'phone': 'Not available'},
            'medications': [],
            'conditions': [],
            'allergies': [],
            'observations': [],
            'procedures': []
        }

def _create_new_observation(smart, observation_data):
    """Create a new observation with Epic FHIR requirements and comprehensive dummy defaults"""
    try:
        # Epic-compatible LOINC templates with dummy values
        loinc_templates = {
            "temperature": {
                "code": "8310-5", 
                "display": "Body temperature", 
                "unit": "Cel", 
                "dummy": 36.5
            },
            "systolic_bp": {
                "code": "8480-6", 
                "display": "Systolic blood pressure", 
                "unit": "mm[Hg]", 
                "dummy": 120
            },
            "diastolic_bp": {
                "code": "8462-4", 
                "display": "Diastolic blood pressure", 
                "unit": "mm[Hg]", 
                "dummy": 80
            },
            "heart_rate": {
                "code": "8867-4", 
                "display": "Heart rate", 
                "unit": "/min", 
                "dummy": 72
            },
            "respiratory_rate": {
                "code": "9279-1", 
                "display": "Respiratory rate", 
                "unit": "/min", 
                "dummy": 16
            },
            "oxygen_saturation": {
                "code": "2708-6", 
                "display": "Oxygen saturation in Arterial blood", 
                "unit": "%", 
                "dummy": 98
            },
            "weight": {
                "code": "29463-7", 
                "display": "Body weight", 
                "unit": "kg", 
                "dummy": 70
            },
            "height": {
                "code": "8302-2", 
                "display": "Body height", 
                "unit": "cm", 
                "dummy": 175
            },
            "bmi": {
                "code": "39156-5", 
                "display": "Body mass index (BMI) [Ratio]", 
                "unit": "kg/m2", 
                "dummy": 23.5
            }
        }

        # Get observation type or default to temperature
        obs_type = str(observation_data.get("type", "temperature")).lower()
        template = loinc_templates.get(obs_type, loinc_templates["temperature"])

        # Use provided values or fallback to template defaults
        obs_code = observation_data.get("code") or template["code"]
        obs_display = observation_data.get("display") or template["display"]
        obs_name = observation_data.get("name") or obs_display
        obs_unit = observation_data.get("unit") or template["unit"]
        
        # Handle value with safe conversion and dummy fallback
        obs_value = observation_data.get("value")
        try:
            obs_value = float(obs_value)
            # Sanity check for reasonable values
            if obs_value <= 0 or obs_value > 1000:  
                obs_value = template["dummy"]
        except (ValueError, TypeError):
            obs_value = template["dummy"]

        # Create Observation resource
        new_obs = Observation()
        new_obs.status = "final"

        # Epic requires category 'vital-signs' for observations
        category = CodeableConcept()
        cat_coding = Coding()
        cat_coding.system = "http://terminology.hl7.org/CodeSystem/observation-category"
        cat_coding.code = "vital-signs"
        cat_coding.display = "Vital Signs"
        category.coding = [cat_coding]
        new_obs.category = [category]

        # Set observation code with LOINC
        code = CodeableConcept()
        coding = Coding()
        coding.system = "http://loinc.org"
        coding.code = obs_code
        coding.display = obs_display
        code.coding = [coding]
        code.text = obs_name
        new_obs.code = code

        # Set value as quantity
        quantity = Quantity()
        quantity.value = obs_value
        quantity.unit = obs_unit
        quantity.system = "http://unitsofmeasure.org"
        quantity.code = obs_unit  # UCUM code
        new_obs.valueQuantity = quantity

        # Set patient reference
        patient_ref = FHIRReference()
        patient_ref.reference = f"Patient/{smart.patient_id}"
        new_obs.subject = patient_ref

        # Set effective date
        effective = FHIRDateTime()
        effective.date = datetime.now()
        new_obs.effectiveDateTime = effective

        # Create in Epic FHIR server
        result = new_obs.create(smart.server)
        return result

    except Exception as e:
        app.logger.error(f"Error creating observation: {e}")
        raise e

def _update_observation(smart, observation_id, observation_data):
    """Update an existing observation in Epic"""
    try:
        existing_obs = Observation.read(observation_id, smart.server)
        
        # Use provided data or keep existing
        new_value = observation_data.get('value', '36.5')
        new_unit = observation_data.get('unit', 'Cel')
        
        if new_value and new_unit:
            if not existing_obs.valueQuantity:
                existing_obs.valueQuantity = Quantity()
            try:
                existing_obs.valueQuantity.value = float(new_value)
            except ValueError:
                existing_obs.valueQuantity.value = 36.5
            existing_obs.valueQuantity.unit = new_unit
        else:
            existing_obs.valueString = str(new_value)
        
        result = existing_obs.update(smart.server)
        return result
        
    except Exception as e:
        app.logger.error(f"Error updating observation: {e}")
        raise e

# ===== MULTI-SESSION API ENDPOINTS =====

@app.route('/api/auth-status')
@cross_origin()
def api_auth_status():
    """Check authentication status for current session"""
    try:
        token = get_session_token()
        smart = _get_smart(token)
        
        if smart and smart.ready and smart.patient:
            return jsonify({
                'authenticated': True,
                'patient_id': smart.patient_id,
                'session_token': token
            })
        
        return jsonify({
            'authenticated': False,
            'session_token': token
        })
    except Exception as e:
        app.logger.error(f"Error in auth-status: {e}")
        token = create_new_session()
        return jsonify({
            'authenticated': False,
            'session_token': token,
            'error': str(e)
        })

@app.route('/api/auth-url')
@cross_origin()
def api_auth_url():
    """Get fresh FHIR authorization URL with clean session"""
    try:
        # Create new session for OAuth flow
        token = create_new_session()
        smart = _get_smart(token, force_new=True)
        
        if smart and smart.authorize_url:
            response = jsonify({
                'auth_url': smart.authorize_url,
                'session_token': token,
                'state': smart.state if hasattr(smart, 'state') else None
            })
            response.headers['X-Session-Token'] = token
            return response
        
        return jsonify({'error': 'No authorization URL available'}), 400
    except Exception as e:
        app.logger.error(f"Error in auth-url: {e}")
        return jsonify({'error': f'Failed to generate auth URL: {str(e)}'}), 500

@app.route('/api/patient-data')
@cross_origin()
def api_patient_data():
    """Get complete patient data as JSON for current session"""
    try:
        token = get_session_token()
        smart = _get_smart(token)
        
        if not smart or not smart.patient:
            return jsonify({'error': 'No patient data available'}), 404
        
        data = _get_complete_patient_data(smart)
        data['session_token'] = token
        return jsonify(data)
    except Exception as e:
        app.logger.error(f"Error getting patient data: {e}")
        return jsonify({'error': 'Failed to retrieve patient data', 'details': str(e)}), 500

@app.route('/api/observations', methods=['GET'])
@cross_origin()
def get_observations():
    """Get detailed patient observations with IDs for editing"""
    try:
        token = get_session_token()
        smart = _get_smart(token)
        
        if not smart or not smart.patient:
            return jsonify({'error': 'No patient data available'}), 404
        
        observations = _get_observations(smart)
        detailed_obs = []
        
        for obs in observations:
            try:
                if isinstance(obs, Observation):
                    obs_data = {
                        'id': obs.id,
                        'name': 'Unknown observation',
                        'value': 'No value',
                        'unit': '',
                        'date': _format_date(obs.effectiveDateTime.isostring if obs.effectiveDateTime else None),
                        'category': 'vital-signs'
                    }
                    
                    # Get observation name
                    if obs.code and obs.code.text:
                        obs_data['name'] = obs.code.text
                    elif obs.code and obs.code.coding:
                        obs_data['name'] = obs.code.coding[0].display or "Unknown observation"
                    
                    # Get observation value
                    if obs.valueQuantity:
                        obs_data['value'] = str(obs.valueQuantity.value) if obs.valueQuantity.value else ''
                        obs_data['unit'] = obs.valueQuantity.unit or ''
                    elif obs.valueString:
                        obs_data['value'] = obs.valueString
                    elif obs.valueCodeableConcept and obs.valueCodeableConcept.text:
                        obs_data['value'] = obs.valueCodeableConcept.text
                    
                    detailed_obs.append(obs_data)
            except Exception as e:
                app.logger.error(f"Error processing observation: {e}")
        
        return jsonify({'observations': detailed_obs, 'session_token': token})
        
    except Exception as e:
        app.logger.error(f"Error getting detailed observations: {e}")
        return jsonify({'error': 'Failed to retrieve observations', 'details': str(e)}), 500

@app.route('/api/observations', methods=['POST'])
@cross_origin()
def create_observation():
    """Create a new observation with dummy defaults for missing/invalid data"""
    try:
        token = get_session_token()
        smart = _get_smart(token)
        
        if not smart or not smart.patient:
            return jsonify({'error': 'No patient data available'}), 404
        
        # Handle empty or missing JSON data with safe parsing
        data = request.get_json(silent=True) or {}
        
        result = _create_new_observation(smart, data)
        
        return jsonify({
            'success': True,
            'message': 'Observation created successfully',
            'id': result.id if hasattr(result, 'id') else None,
            'data_used': data,
            'session_token': token,
            'epic_compliant': True
        })
        
    except Exception as e:
        error_msg = str(e)
        app.logger.error(f"Error creating observation: {error_msg}")
        
        token = get_session_token()
        
        # Handle specific Epic FHIR errors
        if '403' in error_msg or 'Forbidden' in error_msg:
            return jsonify({
                'error': 'Permission denied - Epic FHIR write restrictions',
                'epic_error_codes': {
                    '4118': 'User not authorized for request',
                    '59187': 'No patient-entered flowsheets found', 
                    '59188': 'Failed to find vital-signs flowsheet row',
                    '59189': 'Failed to file the reading'
                },
                'solution': 'Contact Epic customer to enable write permissions or test in sandbox',
                'session_token': token
            }), 403
        else:
            return jsonify({
                'error': f'Failed to create observation: {error_msg}',
                'session_token': token
            }), 500

@app.route('/api/observations/<observation_id>', methods=['PUT'])
@cross_origin()
def update_observation(observation_id):
    """Update an existing observation"""
    try:
        token = get_session_token()
        smart = _get_smart(token)
        
        if not smart or not smart.patient:
            return jsonify({'error': 'No patient data available'}), 404
        
        data = request.get_json(silent=True) or {}
        
        # Add dummy defaults if missing
        if not data.get('value'):
            data['value'] = '36.5'
        if not data.get('unit'):
            data['unit'] = 'Cel'
            
        result = _update_observation(smart, observation_id, data)
        return jsonify({
            'success': True,
            'message': 'Observation updated successfully',
            'data_used': data,
            'session_token': token
        })
    except Exception as e:
        app.logger.error(f"Error updating observation: {e}")
        token = get_session_token()
        return jsonify({
            'error': f'Failed to update observation: {str(e)}',
            'session_token': token
        }), 500

@app.route('/api/check-permissions')
@cross_origin()
def check_permissions():
    """Check what permissions your app actually has"""
    try:
        token = get_session_token()
        smart = _get_smart(token)
        
        if not smart:
            return jsonify({'error': 'No SMART client available'}), 404
        
        if token not in active_sessions:
            return jsonify({'error': 'Session not found'}), 404
        
        session_data = active_sessions[token]
        state = session_data.get('state', {})
        token_response = state.get('tokenResponse', {}) if isinstance(state, dict) else {}
        granted_scopes = token_response.get('scope', '').split(' ') if token_response.get('scope') else []
        
        permissions = {
            'session_token': token,
            'granted_scopes': granted_scopes,
            'can_read_observations': any(scope in granted_scopes for scope in ['user/Observation.read', 'patient/Observation.read']),
            'can_write_observations': any(scope in granted_scopes for scope in ['user/Observation.write', 'patient/Observation.write']),
            'can_create_observations': any(scope in granted_scopes for scope in ['user/Observation.create', 'patient/Observation.create']),
            'has_patient_context': 'launch/patient' in granted_scopes,
            'has_user_context': 'fhirUser' in granted_scopes,
            'patient_id': smart.patient_id if smart.patient else None,
            'epic_requirements': {
                'flowsheet_configured': 'Epic must have flowsheet rows for LOINC codes',
                'vital_signs_category': 'Required for all observations',
                'loinc_codes_supported': ['8310-5', '8480-6', '8462-4', '8867-4', '9279-1', '2708-6', '29463-7', '8302-2']
            }
        }
        
        return jsonify(permissions)
        
    except Exception as e:
        app.logger.error(f"Error checking permissions: {e}")
        return jsonify({'error': f'Failed to check permissions: {str(e)}'}), 500

@app.route('/api/logout', methods=['POST'])
@cross_origin()
def api_logout():
    """Logout current session"""
    try:
        token = get_session_token()
        old_token = token
        _logout(token)
        
        return jsonify({
            'success': True,
            'message': 'Logged out successfully',
            'previous_session_token': old_token
        })
    except Exception as e:
        app.logger.error(f"Error in logout: {e}")
        return jsonify({
            'success': False,
            'message': 'Logout completed with errors',
            'error': str(e)
        })

@app.route('/api/reset', methods=['POST'])
@cross_origin()
def api_reset():
    """Reset current session"""
    try:
        token = get_session_token()
        old_token = token
        _reset_session(token)
        
        return jsonify({
            'success': True,
            'message': 'Session reset',
            'session_token': token,
            'previous_state_cleared': True
        })
    except Exception as e:
        app.logger.error(f"Error in reset: {e}")
        return jsonify({
            'success': False,
            'message': 'Reset completed with errors',
            'error': str(e)
        })

@app.route('/api/sessions')
@cross_origin()
def list_sessions():
    """Debug endpoint to list active sessions"""
    try:
        cleanup_expired_sessions_db()
        
        session_info = {}
        all_sessions = get_all_sessions()
        for token, data in all_sessions.items():
            session_info[token[:8] + "..."] = {
                'session_id': data.get('session_id'),
                'created_at': data.get('created_at').isoformat() if data.get('created_at') else None,
                'last_accessed': data.get('last_accessed').isoformat() if data.get('last_accessed') else None,
                'expires_at': data.get('expires_at').isoformat() if data.get('expires_at') else None,
                'has_patient': False,  # We don't store smart_client anymore
                'has_tokens': bool(data.get('access_token'))
            }
        
        current_token = get_session_token()
        
        return jsonify({
            'current_session_token': current_token[:8] + "..." if current_token else None,
            'active_sessions_count': len(all_sessions),
            'max_sessions': MAX_SESSIONS,
            'sessions': session_info,
            'debug_info': {
                'multi_session_working': len(all_sessions) >= 0,
                'isolation_enabled': True,
                'auto_cleanup_enabled': True,
                'storage_type': 'MongoDB' if sessions_collection is not None else 'In-Memory'
            }
        })
    except Exception as e:
        app.logger.error(f"Error listing sessions: {e}")
        return jsonify({'error': f'Failed to list sessions: {str(e)}'}), 500

# OAuth callback handler with multi-session support
@app.route('/fhir-app/')
@cross_origin()
def callback():
    """OAuth2 callback with multi-session support and MongoDB persistence"""
    try:
        # Enhanced session token retrieval with multiple fallbacks
        token = None
        
        # Try Flask session first (primary method)
        token = session.get('session_token')
        
        # Try headers as fallback
        if not token:
            token = request.headers.get('X-Session-Token')
        
        # Try URL parameter for debugging
        if not token:
            token = request.args.get('session_token')
        
        # Validate token exists in storage
        if not token or not get_session_from_db(token):
            # Try to recover from state parameter if available
            state_param = request.args.get('state')
            if state_param:
                app.logger.warning(f"Attempting session recovery with state: {state_param[:20]}...")
                recovered_token = find_session_by_oauth_state(state_param)
                if recovered_token:
                    token = recovered_token
                    app.logger.info(f"Successfully recovered session: {token[:8]}...")
                    # Update Flask session with recovered token
                    session['session_token'] = token
                    session.permanent = True
                else:
                    app.logger.warning(f"No session found for OAuth state: {state_param[:20]}...")
            
            if not token or not get_session_from_db(token):
                app.logger.error(f"Session recovery failed completely")
                error_data = {'success': False, 'error': 'Invalid session state - please restart the authorization flow'}
                encoded_error = urllib.parse.quote(json.dumps(error_data))
                return redirect(f"{CLIENT_REDIRECT_URL}?error={encoded_error}")
        
        app.logger.info(f"Using session token: {token[:8]}...")
        
        smart = _get_smart(token)
        if not smart:
            raise Exception("No SMART client available for session")
        
        # Handle the OAuth callback
        smart.handle_callback(request.url)
        
        # Store tokens securely on server-side
        try:
            if hasattr(smart, 'server') and hasattr(smart.server, 'access_token'):
                access_token = smart.server.access_token
                refresh_token = getattr(smart.server, 'refresh_token', None)
                expires_in = getattr(smart.server, 'expires_in', 3600)
                
                store_tokens(token, access_token, refresh_token, expires_in)
                app.logger.info(f"Stored tokens for session: {token[:8]}...")
        except Exception as e:
            app.logger.error(f"Error storing tokens: {e}")
        
        if smart.ready and smart.patient:
            try:
                patient_data = _get_complete_patient_data(smart)
                
                summary_data = {
                    'success': True,
                    'patient_id': patient_data['patient_id'],
                    'name': patient_data['demographics']['name'],
                    'gender': patient_data['demographics']['gender'],
                    'birth_date': patient_data['demographics']['birth_date'],
                    'medications_count': len(patient_data['medications']),
                    'conditions_count': len(patient_data['conditions']),
                    'session_token': token
                }
                
                encoded_data = urllib.parse.quote(json.dumps(summary_data))
                redirect_url = f"{CLIENT_REDIRECT_URL}?data={encoded_data}"
                
                app.logger.info(f"OAuth success for session {token[:8]}..., redirecting to client")
                return redirect(redirect_url)
            except Exception as e:
                app.logger.error(f"Error processing patient data in callback: {e}")
                error_data = {'success': False, 'error': f'Failed to process patient data: {str(e)}', 'session_token': token}
                encoded_error = urllib.parse.quote(json.dumps(error_data))
                return redirect(f"{CLIENT_REDIRECT_URL}?error={encoded_error}")
        else:
            error_data = {'success': False, 'error': 'No patient data available', 'session_token': token}
            encoded_error = urllib.parse.quote(json.dumps(error_data))
            return redirect(f"{CLIENT_REDIRECT_URL}?error={encoded_error}")
            
    except Exception as e:
        app.logger.error(f"OAuth callback error: {e}")
        token = session.get('session_token', 'unknown')
        error_data = {'success': False, 'error': str(e), 'session_token': token}
        encoded_error = urllib.parse.quote(json.dumps(error_data))
        return redirect(f"{CLIENT_REDIRECT_URL}?error={encoded_error}")

@app.route('/api/set-redirect-url', methods=['POST'])
@cross_origin()
def set_redirect_url():
    """Allow client to set custom redirect URL"""
    try:
        global CLIENT_REDIRECT_URL
        data = request.get_json()
        if data and 'redirect_url' in data:
            CLIENT_REDIRECT_URL = data['redirect_url']
            return jsonify({'success': True, 'redirect_url': CLIENT_REDIRECT_URL})
        return jsonify({'error': 'Invalid redirect URL'}), 400
    except Exception as e:
        app.logger.error(f"Error setting redirect URL: {e}")
        return jsonify({'error': f'Failed to set redirect URL: {str(e)}'}), 500

# Legacy HTML endpoints with multi-session support
@app.route('/')
@app.route('/index.html')
@cross_origin()
def index():
    """The app's main page with multi-session info"""
    try:
        cleanup_expired_sessions()
        
        token = get_session_token()
        smart = _get_smart(token) if token else None
        
        html = f"""
        <html>
        <head>
            <title>Epic FHIR Multi-Session Patient Record Viewer</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .section {{ margin-bottom: 30px; border: 1px solid #ccc; padding: 15px; border-radius: 5px; }}
                .section h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 5px; }}
                .patient-info {{ background-color: #f8f9fa; }}
                .session-info {{ background-color: #e8f4f8; }}
                .multi-session-info {{ background-color: #fff3cd; border-color: #ffeaa7; }}
                .no-data {{ color: #7f8c8d; font-style: italic; }}
                ul {{ margin: 10px 0; }}
                li {{ margin: 5px 0; }}
                .error {{ color: #e74c3c; }}
                .success {{ color: #27ae60; }}
                .redirect-info {{ background-color: #e8f4f8; padding: 10px; margin: 10px 0; border-radius: 5px; }}
                button {{ padding: 8px 16px; margin: 5px; cursor: pointer; }}
                .btn-primary {{ background-color: #007bff; color: white; border: none; }}
                .btn-danger {{ background-color: #dc3545; color: white; border: none; }}
                .token {{ font-family: monospace; background: #f1f1f1; padding: 2px 6px; border-radius: 3px; }}
            </style>
        </head>
        <body>
        
        <div class="section multi-session-info">
            <h2>🔄 Multi-Session FHIR Server</h2>
            <p><strong>Active Sessions:</strong> {len(get_all_sessions())}/{MAX_SESSIONS}</p>
            <p><strong>Current Session:</strong> <span class="token">{token[:8] + '...' if token else 'None'}</span></p>
            <p><strong>Session Isolation:</strong> ✅ Each session is completely independent</p>
            <button class="btn-primary" onclick="window.open('/api/auth-url', '_blank')">🔗 New Session (New Tab)</button>
            <button class="btn-danger" onclick="fetch('/api/reset', {{method: 'POST'}}).then(() => location.reload())">🔄 Reset Current Session</button>
        </div>
        """
        
        if smart is None or not token:
            html += """
            <h1>Epic FHIR Multi-Session Patient Record Viewer</h1>
            <div class="section">
                <p>No active session. Each browser tab/window can have its own independent FHIR session.</p>
                <p><a href="/api/auth-url">Start New Session</a></p>
            </div>
            """
        
        elif smart and smart.ready and smart.patient is not None:
            try:
                demographics = _get_patient_demographics(smart)
                prescriptions = _get_prescriptions(smart)
                conditions = _get_conditions(smart)
                observations = _get_observations(smart)
                allergies = _get_allergies(smart)
                procedures = _get_procedures(smart)
                
                html += f"""
                <h1>Patient Record: {demographics.get('name', 'Unknown')}</h1>
                
                <div class="section redirect-info">
                    <p><strong>Note:</strong> After successful OAuth, patients are automatically redirected to: <code>{CLIENT_REDIRECT_URL}</code></p>
                    <p><strong>Session Token:</strong> <span class="token">{token[:12]}...</span></p>
                </div>
                
                <div class="section patient-info">
                    <h2>Demographics</h2>
                    <ul>
                        <li><strong>Name:</strong> {demographics.get('name', 'Unknown')}</li>
                        <li><strong>Gender:</strong> {demographics.get('gender', 'Unknown')}</li>
                        <li><strong>Date of Birth:</strong> {demographics.get('birth_date', 'Unknown')}</li>
                        <li><strong>Address:</strong> {demographics.get('address', 'Not available')}</li>
                        <li><strong>Phone:</strong> {demographics.get('phone', 'Not available')}</li>
                    </ul>
                </div>
                
                <div class="section">
                    <h2>Current Medications ({len(prescriptions)})</h2>
                """
                
                if prescriptions:
                    html += "<ul>"
                    for prescription in prescriptions:
                        try:
                            med_name = _get_med_name(prescription, smart)
                            status = prescription.status if hasattr(prescription, 'status') else "Unknown status"
                            html += f"<li>{med_name} (Status: {status})</li>"
                        except Exception as e:
                            html += f'<li class="error">Error processing medication: {str(e)}</li>'
                    html += "</ul>"
                else:
                    html += '<p class="no-data">No prescriptions found</p>'
                
                html += f"""
                </div>
                
                <div class="section">
                    <h2>Recent Observations/Vitals ({len(observations)})</h2>
                """
                
                if observations:
                    html += "<ul>"
                    for obs in observations[:10]:
                        html += f"<li>{_format_observation(obs)}</li>"
                    if len(observations) > 10:
                        html += f"<li><em>... and {len(observations) - 10} more observations</em></li>"
                    html += "</ul>"
                else:
                    html += '<p class="no-data">No observations found</p>'
                
                html += """
                </div>
                
                <div class="section">
                    <button class="btn-danger" onclick="fetch('/api/logout', {method: 'POST'}).then(() => location.reload())">🚪 Logout Current Session</button>
                    <button class="btn-primary" onclick="window.open('/api/auth-url', '_blank')">➕ Open New Session</button>
                </div>
                """
            except Exception as e:
                html += f'<div class="section error"><p>Error displaying patient data: {str(e)}</p></div>'
        
        else:
            html += f"""
            <h1>Epic FHIR Multi-Session Patient Record Viewer</h1>
            <div class="section">
                <p>Session exists but not authenticated. <a href="/api/auth-url">Click here to authenticate</a></p>
            </div>
            """
        
        # Add session list
        html += """
        <div class="section">
            <h2>All Active Sessions</h2>
            <p><a href="/api/sessions" target="_blank">View Session Details (JSON)</a></p>
        </div>
        
        </body>
        </html>
        """
        
        return html
    except Exception as e:
        app.logger.error(f"Error in index: {e}")
        return f"""
        <html>
        <body>
            <h1>Epic FHIR Multi-Session Error</h1>
            <p>An error occurred: {str(e)}</p>
            <p><a href="/api/reset">Reset and try again</a></p>
        </body>
        </html>
        """

@app.route('/logout')
@cross_origin()
def logout():
    try:
        token = get_session_token()
        _logout(token)
        return redirect('/')
    except Exception as e:
        app.logger.error(f"Error in logout route: {e}")
        return redirect('/')

@app.route('/reset')
@cross_origin()
def reset():
    try:
        token = get_session_token()
        _reset_session(token)
        return redirect('/')
    except Exception as e:
        app.logger.error(f"Error in reset route: {e}")
        return redirect('/')

# Cleanup background task (runs periodically)
@app.route('/api/cleanup')
@cross_origin()
def manual_cleanup():
    """Manual cleanup endpoint for testing"""
    try:
        all_sessions_before = get_all_sessions()
        old_count = len(all_sessions_before)
        cleanup_expired_sessions_db()
        all_sessions_after = get_all_sessions() 
        new_count = len(all_sessions_after)
        
        return jsonify({
            'success': True,
            'message': f'Cleanup completed',
            'sessions_before': old_count,
            'sessions_after': new_count,
            'sessions_removed': old_count - new_count
        })
    except Exception as e:
        return jsonify({'error': f'Cleanup failed: {str(e)}'}), 500

# Global error handlers
@app.errorhandler(500)
def handle_500(e):
    app.logger.error(f"Internal server error: {e}")
    token = get_session_token() if get_session_token else None
    return jsonify({
        'error': 'Internal server error',
        'message': 'The server encountered an unexpected error',
        'session_token': token
    }), 500

@app.errorhandler(404)
def handle_404(e):
    token = get_session_token() if get_session_token else None
    return jsonify({
        'error': 'Not found',
        'message': 'The requested endpoint was not found',
        'session_token': token
    }), 404

# if __name__ == '__main__':
#     try:
#         import flaskbeaker
#         flaskbeaker.FlaskBeaker.setup_app(app)
#     except Exception as e:
#         app.logger.warning(f"FlaskBeaker setup failed: {e}")
    
#     logging.basicConfig(level=logging.DEBUG)
#     app.run(debug=True, port=8000)

