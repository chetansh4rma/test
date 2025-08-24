# Deployment Guide for Fixed FHIR Flask App

## ✅ **What Was Fixed**

1. **Session Management**: Replaced Flask built-in sessions with `flask-session` + MongoDB
2. **Cookie Configuration**: Fixed for cross-site Epic redirects (`SameSite=None`, `Secure=True`)
3. **Gunicorn Compatibility**: Sessions now work across multiple worker processes
4. **OAuth Recovery**: Better session state handling for Epic callbacks

## 🚀 **Quick Deploy Steps**

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Set Environment Variables
```bash
# Production settings
FLASK_ENV=production
SECRET_KEY=your-super-secret-key-here
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/
ALLOWED_ORIGINS=https://yourdomain.com,https://epic.com
CLIENT_REDIRECT_URL=https://yourdomain.com/fhir
```

### 3. Deploy with Gunicorn
```bash
gunicorn --config gunicorn.conf.py flask_app:app
```

## 🔍 **Test the Fix**

After deployment, test these endpoints:

1. **`/api/debug-session`** - Check current session state
2. **`/api/debug-sessions`** - Verify MongoDB session storage  
3. **`/api/auth-url`** - Test OAuth URL generation

## 🎯 **Why This Fixes Your Issue**

- **✅ Gunicorn Workers**: MongoDB sessions shared across all workers
- **✅ Cross-Site Cookies**: `SameSite=None` allows Epic redirects
- **✅ HTTPS Cookies**: `Secure=True` ensures cookies work in production
- **✅ Session Persistence**: Sessions stored in MongoDB, not worker memory

Your "Invalid session state" error should now be resolved! 🎉
