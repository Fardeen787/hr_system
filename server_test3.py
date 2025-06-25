# api_server_cloudflare.py - With Cloudflare Tunnel Support and Resume Storage
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import json
from functools import wraps
import logging
import re
import subprocess
import threading
import time
import os
import signal
import sys
import shutil
from werkzeug.utils import secure_filename

# ============================================
# CONFIGURATION - HARDCODED
# ============================================

# MySQL Database Configuration
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Khan@123',
    'database': 'hiring_bot',
}

# API Configuration
API_KEY = "sk-hiring-bot-2024-secret-key-xyz789"  # Your secret API key
API_PORT = 5000  # Port number for the API server

# Cloudflare Tunnel Configuration
CLOUDFLARE_TUNNEL_NAME = "hiring-bot-api"  # Name for your tunnel
CLOUDFLARE_TUNNEL_URL = None  # Will be set after tunnel starts

# File Storage Configuration
BASE_STORAGE_PATH = "approved_tickets"  # Base folder for storing approved tickets
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'rtf'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB max file size

# ============================================
# Flask App Initialization
# ============================================

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for cross-origin requests
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create base storage directory if it doesn't exist
if not os.path.exists(BASE_STORAGE_PATH):
    os.makedirs(BASE_STORAGE_PATH)
    logger.info(f"Created base storage directory: {BASE_STORAGE_PATH}")

# ============================================
# File Storage Helper Functions
# ============================================

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def create_ticket_folder(ticket_id, ticket_subject=None):
    """Create a folder for approved ticket"""
    try:
        # Clean ticket subject for folder name
        if ticket_subject:
            # Remove special characters and limit length
            clean_subject = re.sub(r'[^\w\s-]', '', ticket_subject)
            clean_subject = re.sub(r'[-\s]+', '-', clean_subject)
            clean_subject = clean_subject[:50].strip('-')
            folder_name = f"{ticket_id}_{clean_subject}"
        else:
            folder_name = str(ticket_id)
        
        folder_path = os.path.join(BASE_STORAGE_PATH, folder_name)
        
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            logger.info(f"Created folder for ticket {ticket_id}: {folder_path}")
            
            # Create a metadata file
            metadata = {
                'ticket_id': ticket_id,
                'created_at': datetime.now().isoformat(),
                'folder_name': folder_name,
                'resumes': []
            }
            
            metadata_path = os.path.join(folder_path, 'metadata.json')
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Also save job details
            save_job_details_to_folder(ticket_id, folder_path)
        
        return folder_path
        
    except Exception as e:
        logger.error(f"Error creating folder for ticket {ticket_id}: {e}")
        return None

def save_job_details_to_folder(ticket_id, folder_path):
    """Save job details to a JSON file in the ticket folder"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Failed to connect to database for job details")
            return False
        
        cursor = conn.cursor(dictionary=True)
        
        # Get ticket information
        cursor.execute("""
            SELECT * FROM tickets 
            WHERE ticket_id = %s
        """, (ticket_id,))
        
        ticket = cursor.fetchone()
        if not ticket:
            cursor.close()
            conn.close()
            return False
        
        # Get the LATEST value for each field
        cursor.execute("""
            SELECT 
                td1.field_name,
                td1.field_value
            FROM ticket_details td1
            INNER JOIN (
                SELECT field_name, MAX(created_at) as max_created_at
                FROM ticket_details
                WHERE ticket_id = %s
                GROUP BY field_name
            ) td2 ON td1.field_name = td2.field_name 
                 AND td1.created_at = td2.max_created_at
            WHERE td1.ticket_id = %s
        """, (ticket_id, ticket_id))
        
        job_details = {}
        for row in cursor.fetchall():
            job_details[row['field_name']] = row['field_value']
        
        # Convert datetime objects to string
        for key, value in ticket.items():
            if isinstance(value, datetime):
                ticket[key] = value.isoformat()
        
        # Combine ticket info with job details
        complete_job_info = {
            'ticket_info': ticket,
            'job_details': job_details,
            'saved_at': datetime.now().isoformat()
        }
        
        # Save to job_details.json
        job_details_path = os.path.join(folder_path, 'job_details.json')
        with open(job_details_path, 'w', encoding='utf-8') as f:
            json.dump(complete_job_info, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved job details for ticket {ticket_id}")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        logger.error(f"Error saving job details for ticket {ticket_id}: {e}")
        return False

def update_job_details_in_folder(ticket_id):
    """Update job details file when ticket information changes"""
    try:
        # Find the ticket folder
        ticket_folders = [f for f in os.listdir(BASE_STORAGE_PATH) 
                         if f.startswith(f"{ticket_id}_")]
        
        if not ticket_folders:
            logger.error(f"No folder found for ticket {ticket_id}")
            return False
        
        folder_path = os.path.join(BASE_STORAGE_PATH, ticket_folders[0])
        return save_job_details_to_folder(ticket_id, folder_path)
        
    except Exception as e:
        logger.error(f"Error updating job details for ticket {ticket_id}: {e}")
        return False

def save_resume_to_ticket(ticket_id, file, applicant_name=None, applicant_email=None):
    """Save resume to ticket folder"""
    try:
        # Get ticket folder path
        ticket_folders = [f for f in os.listdir(BASE_STORAGE_PATH) 
                         if f.startswith(f"{ticket_id}_")]
        
        if not ticket_folders:
            logger.error(f"No folder found for ticket {ticket_id}")
            return None
        
        folder_path = os.path.join(BASE_STORAGE_PATH, ticket_folders[0])
        
        # Generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_filename = secure_filename(file.filename)
        base_name, ext = os.path.splitext(original_filename)
        
        if applicant_name:
            clean_name = re.sub(r'[^\w\s-]', '', applicant_name)
            clean_name = re.sub(r'[-\s]+', '_', clean_name)
            filename = f"{clean_name}_{timestamp}{ext}"
        else:
            filename = f"resume_{timestamp}{ext}"
        
        file_path = os.path.join(folder_path, filename)
        
        # Save file
        file.save(file_path)
        
        # Update metadata
        metadata_path = os.path.join(folder_path, 'metadata.json')
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            
            resume_info = {
                'filename': filename,
                'original_filename': original_filename,
                'uploaded_at': datetime.now().isoformat(),
                'applicant_name': applicant_name,
                'applicant_email': applicant_email,
                'file_size': os.path.getsize(file_path)
            }
            
            metadata['resumes'].append(resume_info)
            
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        
        logger.info(f"Saved resume {filename} for ticket {ticket_id}")
        return file_path
        
    except Exception as e:
        logger.error(f"Error saving resume for ticket {ticket_id}: {e}")
        return None

def get_ticket_resumes(ticket_id):
    """Get list of resumes for a ticket"""
    try:
        ticket_folders = [f for f in os.listdir(BASE_STORAGE_PATH) 
                         if f.startswith(f"{ticket_id}_")]
        
        if not ticket_folders:
            return []
        
        folder_path = os.path.join(BASE_STORAGE_PATH, ticket_folders[0])
        metadata_path = os.path.join(folder_path, 'metadata.json')
        
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
                return metadata.get('resumes', [])
        
        return []
        
    except Exception as e:
        logger.error(f"Error getting resumes for ticket {ticket_id}: {e}")
        return []

# ============================================
# Cloudflare Tunnel Functions (same as before)
# ============================================

def check_cloudflared_installed():
    """Check if cloudflared is installed"""
    try:
        result = subprocess.run(['cloudflared', 'version'], 
                              capture_output=True, text=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False

def install_cloudflared():
    """Install cloudflared if not present"""
    print("\n" + "="*60)
    print("📦 Installing Cloudflare Tunnel (cloudflared)...")
    print("="*60)
    
    system = sys.platform
    
    try:
        if system == "linux" or system == "linux2":
            # Linux installation
            commands = [
                "wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb",
                "sudo dpkg -i cloudflared-linux-amd64.deb",
                "rm cloudflared-linux-amd64.deb"
            ]
            for cmd in commands:
                subprocess.run(cmd, shell=True, check=True)
                
        elif system == "darwin":
            # macOS installation
            subprocess.run("brew install cloudflare/cloudflare/cloudflared", 
                         shell=True, check=True)
                         
        elif system == "win32":
            # Windows installation
            print("Please download cloudflared from:")
            print("https://github.com/cloudflare/cloudflared/releases")
            print("Add it to your PATH and restart the script.")
            return False
            
        print("✅ Cloudflared installed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install cloudflared: {e}")
        print("\nPlease install manually:")
        print("https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation")
        return False

def start_cloudflare_tunnel():
    """Start Cloudflare tunnel and return public URL"""
    global CLOUDFLARE_TUNNEL_URL
    
    if not check_cloudflared_installed():
        if not install_cloudflared():
            return None
    
    print("\n" + "="*60)
    print("🌐 Starting Cloudflare Tunnel...")
    print("="*60)
    
    try:
        # Check if user is logged in
        login_check = subprocess.run(['cloudflared', 'tunnel', 'list'], 
                                   capture_output=True, text=True)
        
        if login_check.returncode != 0 or "You need to login" in login_check.stderr:
            print("📝 First time setup - Please login to Cloudflare")
            print("This will open your browser for authentication...")
            subprocess.run(['cloudflared', 'tunnel', 'login'])
            print("✅ Login successful!")
        
        # Try to create tunnel (will fail if exists, which is fine)
        create_result = subprocess.run(
            ['cloudflared', 'tunnel', 'create', CLOUDFLARE_TUNNEL_NAME],
            capture_output=True, text=True
        )
        
        if "already exists" in create_result.stderr:
            print(f"ℹ️  Tunnel '{CLOUDFLARE_TUNNEL_NAME}' already exists")
        elif create_result.returncode == 0:
            print(f"✅ Created tunnel '{CLOUDFLARE_TUNNEL_NAME}'")
        else:
            print(f"⚠️  Tunnel creation: {create_result.stderr}")
        
        # Start the tunnel with try.cloudflare.com for quick testing
        tunnel_process = subprocess.Popen(
            ['cloudflared', 'tunnel', '--url', f'http://localhost:{API_PORT}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait for tunnel to establish and capture URL
        print("⏳ Establishing tunnel connection...")
        
        start_time = time.time()
        while time.time() - start_time < 30:  # 30 second timeout
            line = tunnel_process.stderr.readline()
            
            # Look for the public URL in the output
            if "https://" in line and ".trycloudflare.com" in line:
                # Extract URL from the line
                url_match = re.search(r'https://[^\s]+\.trycloudflare\.com', line)
                if url_match:
                    CLOUDFLARE_TUNNEL_URL = url_match.group(0)
                    break
        
        if CLOUDFLARE_TUNNEL_URL:
            print("\n" + "="*60)
            print("🎉 CLOUDFLARE TUNNEL ACTIVE")
            print("="*60)
            print(f"📱 Public URL: {CLOUDFLARE_TUNNEL_URL}")
            print(f"🔗 Share this URL to access your API from anywhere")
            print(f"🔐 API Key: {API_KEY}")
            print("="*60 + "\n")
            
            # Keep tunnel process running in background
            tunnel_thread = threading.Thread(
                target=monitor_tunnel_process, 
                args=(tunnel_process,),
                daemon=True
            )
            tunnel_thread.start()
            
            return CLOUDFLARE_TUNNEL_URL
        else:
            print("❌ Failed to establish tunnel - timeout")
            tunnel_process.terminate()
            return None
            
    except Exception as e:
        print(f"❌ Error starting tunnel: {e}")
        return None

def monitor_tunnel_process(process):
    """Monitor tunnel process and restart if needed"""
    while True:
        output = process.stderr.readline()
        if output:
            # Log tunnel output for debugging (optional)
            if "error" in output.lower():
                logger.error(f"Tunnel error: {output.strip()}")
        
        # Check if process is still running
        if process.poll() is not None:
            logger.error("Tunnel process died! Restarting...")
            # Could implement restart logic here
            break
        
        time.sleep(1)

def stop_cloudflare_tunnel():
    """Stop all cloudflared processes"""
    try:
        if sys.platform == "win32":
            subprocess.run("taskkill /F /IM cloudflared.exe", shell=True)
        else:
            subprocess.run("pkill cloudflared", shell=True)
        print("✅ Cloudflare tunnel stopped")
    except:
        pass

# ============================================
# Database Helper Functions
# ============================================

def get_db_connection():
    """Create and return database connection"""
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        return conn
    except Error as e:
        logger.error(f"Database connection failed: {e}")
        return None

def serialize_datetime(obj):
    """Convert datetime objects to ISO format strings"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj

# ============================================
# Authentication Decorator
# ============================================

def require_api_key(f):
    """Decorator to require API key for endpoints"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        
        # Also check URL parameter as fallback
        if not api_key:
            api_key = request.args.get('api_key')
        
        if api_key != API_KEY:
            return jsonify({
                'success': False,
                'error': 'Invalid or missing API key'
            }), 401
        
        return f(*args, **kwargs)
    return decorated_function

# ============================================
# New Resume Management Endpoints
# ============================================

@app.route('/api/tickets/<ticket_id>/approve', methods=['POST'])
@require_api_key
def approve_ticket_and_create_folder(ticket_id):
    """Approve a ticket and create its folder"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # Get ticket details
        cursor.execute("""
            SELECT ticket_id, subject, approval_status
            FROM tickets
            WHERE ticket_id = %s
        """, (ticket_id,))
        
        ticket = cursor.fetchone()
        
        if not ticket:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Ticket not found'
            }), 404
        
        # Update approval status if not already approved
        if ticket['approval_status'] != 'approved':
            cursor.execute("""
                UPDATE tickets 
                SET approval_status = 'approved', 
                    approved_at = NOW()
                WHERE ticket_id = %s
            """, (ticket_id,))
            conn.commit()
        
        cursor.close()
        conn.close()
        
        # Create folder for the ticket (which will also save job details)
        folder_path = create_ticket_folder(ticket_id, ticket['subject'])
        
        if folder_path:
            return jsonify({
                'success': True,
                'message': f'Ticket {ticket_id} approved, folder created, and job details saved',
                'folder_path': folder_path
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to create folder'
            }), 500
            
    except Exception as e:
        logger.error(f"Error approving ticket: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tickets/<ticket_id>/update-job-details', methods=['POST'])
@require_api_key
def update_job_details_endpoint(ticket_id):
    """Update job details file when ticket information changes"""
    try:
        success = update_job_details_in_folder(ticket_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'Job details updated for ticket {ticket_id}'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to update job details'
            }), 500
            
    except Exception as e:
        logger.error(f"Error updating job details: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tickets/<ticket_id>/resumes', methods=['POST'])
@require_api_key
def upload_resume(ticket_id):
    """Upload a resume for a specific ticket"""
    try:
        # Check if the ticket exists and is approved
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT ticket_id, subject, approval_status
            FROM tickets
            WHERE ticket_id = %s
        """, (ticket_id,))
        
        ticket = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if not ticket:
            return jsonify({
                'success': False,
                'error': 'Ticket not found'
            }), 404
        
        if ticket['approval_status'] != 'approved':
            return jsonify({
                'success': False,
                'error': 'Ticket must be approved before uploading resumes'
            }), 400
        
        # Check if file is in request
        if 'resume' not in request.files:
            return jsonify({
                'success': False,
                'error': 'No file uploaded'
            }), 400
        
        file = request.files['resume']
        
        if file.filename == '':
            return jsonify({
                'success': False,
                'error': 'No file selected'
            }), 400
        
        if not allowed_file(file.filename):
            return jsonify({
                'success': False,
                'error': f'Invalid file type. Allowed types: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # Get applicant details from form data
        applicant_name = request.form.get('applicant_name')
        applicant_email = request.form.get('applicant_email')
        
        # Ensure folder exists
        folder_path = create_ticket_folder(ticket_id, ticket['subject'])
        if not folder_path:
            return jsonify({
                'success': False,
                'error': 'Failed to create ticket folder'
            }), 500
        
        # Save the resume
        saved_path = save_resume_to_ticket(
            ticket_id, 
            file, 
            applicant_name, 
            applicant_email
        )
        
        if saved_path:
            return jsonify({
                'success': True,
                'message': 'Resume uploaded successfully',
                'file_path': saved_path
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save resume'
            }), 500
            
    except Exception as e:
        logger.error(f"Error uploading resume: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tickets/<ticket_id>/resumes', methods=['GET'])
@require_api_key
def get_resumes(ticket_id):
    """Get list of all resumes for a ticket"""
    try:
        resumes = get_ticket_resumes(ticket_id)
        
        return jsonify({
            'success': True,
            'data': {
                'ticket_id': ticket_id,
                'resume_count': len(resumes),
                'resumes': resumes
            }
        })
        
    except Exception as e:
        logger.error(f"Error getting resumes: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tickets/<ticket_id>/resumes/<filename>', methods=['GET'])
@require_api_key
def download_resume(ticket_id, filename):
    """Download a specific resume"""
    try:
        # Find the ticket folder
        ticket_folders = [f for f in os.listdir(BASE_STORAGE_PATH) 
                         if f.startswith(f"{ticket_id}_")]
        
        if not ticket_folders:
            return jsonify({
                'success': False,
                'error': 'Ticket folder not found'
            }), 404
        
        folder_path = os.path.join(BASE_STORAGE_PATH, ticket_folders[0])
        file_path = os.path.join(folder_path, secure_filename(filename))
        
        if not os.path.exists(file_path):
            return jsonify({
                'success': False,
                'error': 'Resume not found'
            }), 404
        
        return send_file(file_path, as_attachment=True)
        
    except Exception as e:
        logger.error(f"Error downloading resume: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/maintenance/create-folders', methods=['POST'])
@require_api_key
def create_existing_folders_endpoint():
    """Endpoint to create folders for all existing approved tickets"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # Get all approved tickets
        cursor.execute("""
            SELECT ticket_id, subject
            FROM tickets
            WHERE approval_status = 'approved'
        """)
        
        approved_tickets = cursor.fetchall()
        results = {
            'created': [],
            'existing': [],
            'failed': []
        }
        
        for ticket in approved_tickets:
            ticket_id = ticket['ticket_id']
            
            # Check if folder already exists
            ticket_folders = [f for f in os.listdir(BASE_STORAGE_PATH) 
                            if f.startswith(f"{ticket_id}_")]
            
            if ticket_folders:
                results['existing'].append({
                    'ticket_id': ticket_id,
                    'folder': ticket_folders[0]
                })
            else:
                # Create folder
                folder_path = create_ticket_folder(ticket_id, ticket['subject'])
                if folder_path:
                    results['created'].append({
                        'ticket_id': ticket_id,
                        'folder': os.path.basename(folder_path)
                    })
                else:
                    results['failed'].append({
                        'ticket_id': ticket_id,
                        'reason': 'Failed to create folder'
                    })
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'total_approved': len(approved_tickets),
                'folders_created': len(results['created']),
                'folders_existing': len(results['existing']),
                'folders_failed': len(results['failed']),
                'details': results
            }
        })
        
    except Exception as e:
        logger.error(f"Error in create_existing_folders_endpoint: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ============================================
# Public Endpoints (No Auth Required)
# ============================================

@app.route('/', methods=['GET'])
def home():
    """Root endpoint - API information"""
    return jsonify({
        'name': 'Hiring Bot API',
        'version': '1.0',
        'public_url': CLOUDFLARE_TUNNEL_URL,
        'endpoints': {
            'public': [
                'GET /',
                'GET /api/health'
            ],
            'authenticated': [
                'GET /api/jobs/approved',
                'GET /api/jobs/<ticket_id>',
                'GET /api/jobs/search',
                'GET /api/stats',
                'GET /api/locations',
                'GET /api/skills',
                'POST /api/tickets/<ticket_id>/approve',
                'POST /api/tickets/<ticket_id>/resumes',
                'GET /api/tickets/<ticket_id>/resumes',
                'GET /api/tickets/<ticket_id>/resumes/<filename>',
                'POST /api/maintenance/create-folders'
            ]
        },
        'authentication': 'Add X-API-Key header with your API key',
        'api_key_hint': 'Contact admin for API key',
        'tunnel_status': 'active' if CLOUDFLARE_TUNNEL_URL else 'local_only',
        'storage_path': BASE_STORAGE_PATH
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    conn = get_db_connection()
    db_status = "connected" if conn else "disconnected"
    
    if conn:
        conn.close()
    
    # Check storage directory
    storage_status = "accessible" if os.path.exists(BASE_STORAGE_PATH) else "not_found"
    
    return jsonify({
        'status': 'ok' if db_status == "connected" else 'error',
        'database': db_status,
        'tunnel': 'active' if CLOUDFLARE_TUNNEL_URL else 'inactive',
        'public_url': CLOUDFLARE_TUNNEL_URL,
        'storage': storage_status,
        'timestamp': datetime.now().isoformat()
    })

# ============================================
# [Include all existing endpoints from original file]
# ============================================

@app.route('/api/jobs/approved', methods=['GET'])
@require_api_key
def get_approved_jobs():
    """
    Get all approved jobs with pagination and filtering
    Shows the LATEST values for each field
    """
    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        per_page = min(int(request.args.get('per_page', 10)), 50)
        location_filter = request.args.get('location', '')
        skills_filter = request.args.get('skills', '')
        sort_by = request.args.get('sort', 'approved_at')
        order = request.args.get('order', 'desc')
        
        # Validate sort parameters
        allowed_sorts = ['created_at', 'approved_at', 'last_updated']
        if sort_by not in allowed_sorts:
            sort_by = 'approved_at'
        
        if order not in ['asc', 'desc']:
            order = 'desc'
        
        offset = (page - 1) * per_page
        
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # First, get all approved tickets
        cursor.execute("""
            SELECT 
                ticket_id,
                sender,
                subject,
                created_at,
                last_updated,
                approved_at,
                status
            FROM tickets
            WHERE approval_status = 'approved' 
                AND status != 'terminated'
            ORDER BY {} {}
            LIMIT %s OFFSET %s
        """.format(sort_by, order), (per_page, offset))
        
        tickets = cursor.fetchall()
        
        # For each ticket, get the LATEST value for each field
        jobs = []
        for ticket in tickets:
            ticket_id = ticket['ticket_id']
            
            # Get the latest value for each field using a subquery
            cursor.execute("""
                SELECT 
                    td1.field_name,
                    td1.field_value
                FROM ticket_details td1
                INNER JOIN (
                    SELECT field_name, MAX(created_at) as max_created_at
                    FROM ticket_details
                    WHERE ticket_id = %s
                    GROUP BY field_name
                ) td2 ON td1.field_name = td2.field_name 
                     AND td1.created_at = td2.max_created_at
                WHERE td1.ticket_id = %s
            """, (ticket_id, ticket_id))
            
            # Build the job details
            job_details = {}
            for row in cursor.fetchall():
                job_details[row['field_name']] = row['field_value']
            
            # Apply location filter if specified
            if location_filter and job_details.get('location', '').lower() != location_filter.lower():
                continue
            
            # Apply skills filter if specified
            if skills_filter:
                skill_list = [s.strip().lower() for s in skills_filter.split(',')]
                job_skills = job_details.get('required_skills', '').lower()
                if not any(skill in job_skills for skill in skill_list):
                    continue
            
            # Check if this job was updated after approval
            cursor.execute("""
                SELECT COUNT(*) as update_count
                FROM ticket_updates
                WHERE ticket_id = %s AND update_timestamp > %s
            """, (ticket_id, ticket['approved_at']))
            
            update_info = cursor.fetchone()
            updated_after_approval = update_info['update_count'] > 0
            
            # Check if folder exists and get resume count
            resumes = get_ticket_resumes(ticket_id)
            
            # Combine ticket info with job details
            job = {
                'ticket_id': ticket['ticket_id'],
                'sender': ticket['sender'],
                'subject': ticket['subject'],
                'created_at': serialize_datetime(ticket['created_at']),
                'last_updated': serialize_datetime(ticket['last_updated']),
                'approved_at': serialize_datetime(ticket['approved_at']),
                'status': ticket['status'],
                'job_title': job_details.get('job_title', 'NOT_FOUND'),
                'location': job_details.get('location', 'NOT_FOUND'),
                'experience_required': job_details.get('experience_required', 'NOT_FOUND'),
                'salary_range': job_details.get('salary_range', 'NOT_FOUND'),
                'job_description': job_details.get('job_description', 'NOT_FOUND'),
                'required_skills': job_details.get('required_skills', 'NOT_FOUND'),
                'employment_type': job_details.get('employment_type', 'NOT_FOUND'),
                'deadline': job_details.get('deadline', 'NOT_FOUND'),
                'updated_after_approval': updated_after_approval,
                'resume_count': len(resumes),
                'has_folder': len([f for f in os.listdir(BASE_STORAGE_PATH) if f.startswith(f"{ticket_id}_")]) > 0
            }
            
            jobs.append(job)
        
        # Get total count for pagination
        count_query = """
            SELECT COUNT(*) as total
            FROM tickets
            WHERE approval_status = 'approved' 
                AND status != 'terminated'
        """
        cursor.execute(count_query)
        total_count = cursor.fetchone()['total']
        
        cursor.close()
        conn.close()
        
        # Calculate pagination info
        total_pages = (total_count + per_page - 1) // per_page
        
        return jsonify({
            'success': True,
            'data': {
                'jobs': jobs,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': total_count,
                    'total_pages': total_pages,
                    'has_next': page < total_pages,
                    'has_prev': page > 1
                }
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_approved_jobs: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Add all other existing endpoints here (get_job_details, debug_ticket, search_jobs, etc.)
# [Include all remaining endpoints from the original file]

@app.route('/api/jobs/<ticket_id>', methods=['GET'])
@require_api_key
def get_job_details(ticket_id):
    """Get detailed information about a specific job"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # Get ticket information
        cursor.execute("""
            SELECT * FROM tickets 
            WHERE ticket_id = %s
        """, (ticket_id,))
        
        ticket = cursor.fetchone()
        
        if not ticket:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Job not found'
            }), 404
        
        # Get the LATEST value for each field
        cursor.execute("""
            SELECT 
                td1.field_name,
                td1.field_value,
                td1.created_at,
                td1.is_initial
            FROM ticket_details td1
            INNER JOIN (
                SELECT field_name, MAX(created_at) as max_created_at
                FROM ticket_details
                WHERE ticket_id = %s
                GROUP BY field_name
            ) td2 ON td1.field_name = td2.field_name 
                 AND td1.created_at = td2.max_created_at
            WHERE td1.ticket_id = %s
        """, (ticket_id, ticket_id))
        
        current_details = {}
        for row in cursor.fetchall():
            current_details[row['field_name']] = row['field_value']
        
        # Get complete history
        cursor.execute("""
            SELECT field_name, field_value, created_at, is_initial
            FROM ticket_details 
            WHERE ticket_id = %s
            ORDER BY field_name, created_at DESC
        """, (ticket_id,))
        
        all_details = cursor.fetchall()
        
        # Organize history by field
        detail_history = {}
        for row in all_details:
            field_name = row['field_name']
            if field_name not in detail_history:
                detail_history[field_name] = []
            
            detail_history[field_name].append({
                'value': row['field_value'],
                'updated_at': serialize_datetime(row['created_at']),
                'is_initial': row['is_initial']
            })
        
        # Get update history
        cursor.execute("""
            SELECT update_timestamp, updated_fields
            FROM ticket_updates
            WHERE ticket_id = %s
            ORDER BY update_timestamp DESC
        """, (ticket_id,))
        
        updates = []
        for row in cursor.fetchall():
            updates.append({
                'timestamp': serialize_datetime(row['update_timestamp']),
                'fields': json.loads(row['updated_fields']) if row['updated_fields'] else {}
            })
        
        # Convert datetime objects in ticket
        for key, value in ticket.items():
            ticket[key] = serialize_datetime(value)
        
        # Get resume information
        resumes = get_ticket_resumes(ticket_id)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'ticket': ticket,
                'current_details': current_details,
                'history': detail_history,
                'updates': updates,
                'is_approved': ticket['approval_status'] == 'approved',
                'updated_after_approval': len([u for u in updates if u['timestamp'] > ticket['approved_at']]) > 0 if ticket['approved_at'] else False,
                'resumes': resumes,
                'resume_count': len(resumes)
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_job_details: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Include remaining endpoints from original file...
# (debug_ticket, search_jobs, get_statistics, get_locations, get_skills, error handlers)

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'error': 'Endpoint not found'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'error': 'Internal server error'
    }), 500

# ============================================
# Cleanup Handler
# ============================================

def cleanup_on_exit(signum=None, frame=None):
    """Cleanup function to stop tunnel on exit"""
    print("\n🛑 Shutting down...")
    stop_cloudflare_tunnel()
    sys.exit(0)

# Register cleanup handlers
signal.signal(signal.SIGINT, cleanup_on_exit)
signal.signal(signal.SIGTERM, cleanup_on_exit)

# ============================================
# Utility Functions for Existing Tickets
# ============================================

def create_folders_for_existing_approved_tickets():
    """Create folders for all existing approved tickets"""
    try:
        conn = get_db_connection()
        if not conn:
            logger.error("Failed to connect to database")
            return
        
        cursor = conn.cursor(dictionary=True)
        
        # Get all approved tickets
        cursor.execute("""
            SELECT ticket_id, subject
            FROM tickets
            WHERE approval_status = 'approved'
        """)
        
        approved_tickets = cursor.fetchall()
        created_count = 0
        existing_count = 0
        
        print(f"\n📁 Checking {len(approved_tickets)} approved tickets for folders...")
        
        for ticket in approved_tickets:
            ticket_id = ticket['ticket_id']
            
            # Check if folder already exists
            ticket_folders = [f for f in os.listdir(BASE_STORAGE_PATH) 
                            if f.startswith(f"{ticket_id}_")]
            
            if ticket_folders:
                existing_count += 1
                print(f"   ✓ Folder already exists for ticket {ticket_id}")
                # Update job details in existing folder
                folder_path = os.path.join(BASE_STORAGE_PATH, ticket_folders[0])
                save_job_details_to_folder(ticket_id, folder_path)
                print(f"   📄 Updated job details for ticket {ticket_id}")
            else:
                # Create folder (which will also save job details)
                folder_path = create_ticket_folder(ticket_id, ticket['subject'])
                if folder_path:
                    created_count += 1
                    print(f"   ✅ Created folder for ticket {ticket_id}: {os.path.basename(folder_path)}")
                    print(f"   📄 Saved job details for ticket {ticket_id}")
                else:
                    print(f"   ❌ Failed to create folder for ticket {ticket_id}")
        
        cursor.close()
        conn.close()
        
        print(f"\n📊 Summary:")
        print(f"   - New folders created: {created_count}")
        print(f"   - Existing folders: {existing_count}")
        print(f"   - Total approved tickets: {len(approved_tickets)}")
        
    except Exception as e:
        logger.error(f"Error creating folders for existing tickets: {e}")
        print(f"❌ Error: {e}")

# ============================================
# Main Entry Point
# ============================================

if __name__ == '__main__':
    import socket
    
    # Get local IP address
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print("="*60)
    print("HIRING BOT API SERVER WITH CLOUDFLARE TUNNEL & RESUME STORAGE")
    print("="*60)
    print(f"Database: {MYSQL_CONFIG['database']}@{MYSQL_CONFIG['host']}")
    print(f"Local URL: http://localhost:{API_PORT}")
    print(f"Network URL: http://{local_ip}:{API_PORT}")
    print(f"API Key: {API_KEY}")
    print(f"Storage Path: {BASE_STORAGE_PATH}")
    print("="*60)
    
    # Create folders for existing approved tickets
    create_folders_for_existing_approved_tickets()
    
    # Start Cloudflare tunnel
    tunnel_url = start_cloudflare_tunnel()
    
    if tunnel_url:
        print("\n📱 Access your API from anywhere using:")
        print(f"   {tunnel_url}")
        print("\n🔐 Example API calls:")
        print(f"   # Get approved jobs:")
        print(f"   curl -H 'X-API-Key: {API_KEY}' {tunnel_url}/api/jobs/approved")
        print(f"\n   # Approve ticket and create folder:")
        print(f"   curl -X POST -H 'X-API-Key: {API_KEY}' {tunnel_url}/api/tickets/TICKET_ID/approve")
        print(f"\n   # Upload resume:")
        print(f"   curl -X POST -H 'X-API-Key: {API_KEY}' \\")
        print(f"        -F 'resume=@resume.pdf' \\")
        print(f"        -F 'applicant_name=John Doe' \\")
        print(f"        -F 'applicant_email=john@example.com' \\")
        print(f"        {tunnel_url}/api/tickets/TICKET_ID/resumes")
    else:
        print("\n⚠️  Running in local mode only")
        print("   Install cloudflared for public access")
    
    print("\n📚 New Resume Management Endpoints:")
    print("- POST /api/tickets/<id>/approve - Approve ticket & create folder")
    print("- POST /api/tickets/<id>/resumes - Upload resume to ticket")
    print("- GET /api/tickets/<id>/resumes - List all resumes for ticket")
    print("- GET /api/tickets/<id>/resumes/<filename> - Download specific resume")
    
    print("\n📚 Existing API Endpoints:")
    print("- GET /api/jobs/approved - Get all approved jobs")
    print("- GET /api/jobs/<id> - Get specific job details")
    print("- GET /api/jobs/search?q=python - Search jobs")
    print("- GET /api/stats - Get statistics")
    print("- GET /api/locations - Get all locations")
    print("- GET /api/skills - Get all skills")
    print("- GET /api/debug/ticket/<id> - Debug ticket data")
    print("\n✋ Press CTRL+C to stop the server")
    print("="*60 + "\n")
    
    try:
        # Run the Flask server
        app.run(host='0.0.0.0', port=API_PORT, debug=False)
    except KeyboardInterrupt:
        cleanup_on_exit()
    except Exception as e:
        print(f"❌ Server error: {e}")
        cleanup_on_exit()