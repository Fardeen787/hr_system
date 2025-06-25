# backend_with_cloudflare_fixed.py - Fixed version with enhanced debugging

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import json
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import hashlib
from pathlib import Path
import threading
import queue
import sys
import subprocess
import time
import re
import signal
import logging
import traceback

# Import configuration from your resume filter
try:
    from resume_filter import (
        config_list, config_list_basic,
        EnhancedJobTicket, ResumeExtractor, 
        UpdatedResumeFilteringSystem
    )
    FILTER_AVAILABLE = True
except ImportError:
    FILTER_AVAILABLE = False
    print("⚠️ Resume filter not found. Filtering features will be disabled.")

app = Flask(__name__)

# CORS configuration for cloudflare tunnel
CORS(app, 
     origins="*",  # Allow all origins
     allow_headers="*",
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     supports_credentials=True)

# Setup logging with more detail
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BASE_FOLDER = 'jobs-data'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

app.config['BASE_FOLDER'] = BASE_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create base folder with absolute path
BASE_FOLDER_ABS = os.path.abspath(BASE_FOLDER)
os.makedirs(BASE_FOLDER_ABS, exist_ok=True)
print(f"📁 Base folder created at: {BASE_FOLDER_ABS}")

# Database configuration - CHANGE THESE VALUES TO MATCH YOUR SETUP
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Khan@123',  # Add your MySQL password here
    'database': 'resume_filter_db'
}

# Cloudflare Tunnel Configuration
CLOUDFLARE_TUNNEL_NAME = "resume-filter-backend"
CLOUDFLARE_TUNNEL_URL = None  # Will be set after tunnel starts
API_PORT = 5000

# Processing queue
processing_queue = queue.Queue()
filtering_status = {}

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
            print("Download 'cloudflared-windows-amd64.exe', rename to 'cloudflared.exe'")
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
        print("❌ Cloudflared not installed!")
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
            print(f"🔗 Share this URL with your frontend: {CLOUDFLARE_TUNNEL_URL}")
            print(f"⚠️  This URL is temporary and will change on restart")
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
        print("📍 API will only be available locally at http://localhost:5000")
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
# Database Functions (same as before)
# ============================================

def setup_database():
    """Setup MySQL database with all required tables"""
    try:
        # Connect without database first
        conn = mysql.connector.connect(
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password']
        )
        cursor = conn.cursor()
        
        # Create database
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_CONFIG['database']}")
        cursor.execute(f"USE {DB_CONFIG['database']}")
        
        # Job tickets table
        job_tickets_table = """
        CREATE TABLE IF NOT EXISTS job_tickets (
            ticket_id VARCHAR(255) PRIMARY KEY,
            job_title VARCHAR(255) NOT NULL,
            position VARCHAR(255),
            location VARCHAR(255),
            experience_required VARCHAR(100),
            salary_range VARCHAR(255),
            employment_type VARCHAR(100),
            deadline VARCHAR(100),
            job_description TEXT,
            required_skills TEXT,
            nice_to_have TEXT,
            sender VARCHAR(255),
            subject VARCHAR(255),
            status VARCHAR(50) DEFAULT 'posted',
            approved_at TIMESTAMP NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            updated_after_approval BOOLEAN DEFAULT FALSE,
            approval_status VARCHAR(50) DEFAULT 'approved',
            approved BOOLEAN DEFAULT TRUE,
            folder_path VARCHAR(500),
            INDEX idx_status (status),
            INDEX idx_created (created_at DESC)
        )
        """
        cursor.execute(job_tickets_table)
        
        # Applications table
        applications_table = """
        CREATE TABLE IF NOT EXISTS applications (
            application_id INT AUTO_INCREMENT PRIMARY KEY,
            ticket_id VARCHAR(255) NOT NULL,
            applicant_name VARCHAR(255) NOT NULL,
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(50),
            resume_filename VARCHAR(255),
            resume_path VARCHAR(500),
            file_size INT,
            file_hash VARCHAR(64),
            cover_letter TEXT,
            linkedin_url VARCHAR(500),
            portfolio_url VARCHAR(500),
            github_url VARCHAR(500),
            years_of_experience INT DEFAULT 0,
            current_location VARCHAR(255),
            expected_salary VARCHAR(100),
            notice_period VARCHAR(100),
            skills TEXT,
            application_status ENUM('submitted', 'processing', 'processed', 'shortlisted', 'rejected') DEFAULT 'submitted',
            ai_score DECIMAL(5,4),
            submission_ip VARCHAR(45),
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP NULL,
            FOREIGN KEY (ticket_id) REFERENCES job_tickets(ticket_id) ON DELETE CASCADE,
            UNIQUE KEY unique_application (ticket_id, email),
            INDEX idx_status (application_status),
            INDEX idx_ticket (ticket_id)
        )
        """
        cursor.execute(applications_table)
        
        # Resumes table
        resumes_table = """
        CREATE TABLE IF NOT EXISTS resumes (
            resume_id INT AUTO_INCREMENT PRIMARY KEY,
            ticket_id VARCHAR(255),
            file_path VARCHAR(500),
            filename VARCHAR(255),
            file_hash VARCHAR(64),
            resume_text LONGTEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES job_tickets(ticket_id) ON DELETE CASCADE,
            UNIQUE KEY unique_resume (ticket_id, file_hash),
            INDEX idx_ticket_resume (ticket_id)
        )
        """
        cursor.execute(resumes_table)
        
        # Resume scores table
        resume_scores_table = """
        CREATE TABLE IF NOT EXISTS resume_scores (
            score_id INT AUTO_INCREMENT PRIMARY KEY,
            resume_id INT,
            ticket_id VARCHAR(255),
            final_score DECIMAL(5,4),
            skill_score DECIMAL(5,4),
            experience_score DECIMAL(5,4),
            location_score DECIMAL(5,4),
            similarity_score DECIMAL(5,4),
            matched_skills JSON,
            detailed_skill_matches JSON,
            detected_experience_years INT,
            additional_features JSON,
            scoring_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (resume_id) REFERENCES resumes(resume_id) ON DELETE CASCADE,
            FOREIGN KEY (ticket_id) REFERENCES job_tickets(ticket_id) ON DELETE CASCADE,
            INDEX idx_ticket_score (ticket_id, final_score DESC)
        )
        """
        cursor.execute(resume_scores_table)
        
        # Final selections table
        final_selections_table = """
        CREATE TABLE IF NOT EXISTS final_selections (
            selection_id INT AUTO_INCREMENT PRIMARY KEY,
            ticket_id VARCHAR(255),
            resume_id INT,
            `rank` INT,
            selection_reason TEXT,
            selected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES job_tickets(ticket_id) ON DELETE CASCADE,
            FOREIGN KEY (resume_id) REFERENCES resumes(resume_id) ON DELETE CASCADE,
            UNIQUE KEY unique_selection (ticket_id, resume_id),
            INDEX idx_ticket_rank (ticket_id, `rank`)
        )
        """
        cursor.execute(final_selections_table)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("✅ Database setup completed successfully")
        return True
        
    except Error as e:
        print(f"❌ Database setup error: {e}")
        return False


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_hash(filepath):
    """Calculate MD5 hash of file"""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_db_connection():
    """Get MySQL database connection"""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        print(f"Database connection error: {e}")
        return None


def create_job_data_json(ticket_folder: str, job_data: dict):
    """Create job-data.json for resume filter compatibility"""
    job_file = Path(ticket_folder) / "job-data.json"
    
    job_json_data = {
        "ticket_id": job_data['ticket_id'],
        "sender": job_data.get('sender', 'hr@company.com'),
        "subject": job_data.get('subject', f"Re: {job_data['job_title']}"),
        "created_at": job_data.get('created_at', datetime.now().isoformat()),
        "last_updated": job_data.get('last_updated', datetime.now().isoformat()),
        "status": job_data.get('status', 'posted'),
        "initial_details": {
            "job_title": job_data['job_title'],
            "position": job_data.get('position', job_data['job_title']),
            "location": job_data.get('location', ''),
            "experience_required": job_data.get('experience_required', ''),
            "salary_range": job_data.get('salary_range', ''),
            "job_description": job_data.get('job_description', ''),
            "required_skills": job_data.get('required_skills', ''),
            "employment_type": job_data.get('employment_type', 'Full-time'),
            "deadline": job_data.get('deadline', 'Open until filled'),
            "nice_to_have": job_data.get('nice_to_have', ''),
        },
        "updates": [],
        "approval_status": "approved",
        "approved": True,
        "approved_at": job_data.get('approved_at', datetime.now().isoformat()),
        "updated_after_approval": job_data.get('updated_after_approval', False)
    }
    
    with open(job_file, 'w', encoding='utf-8') as f:
        json.dump(job_json_data, f, indent=2)
    
    # Create job-description.txt
    job_desc_file = Path(ticket_folder) / "job-description.txt"
    with open(job_desc_file, 'w', encoding='utf-8') as f:
        f.write(job_data.get('job_description', ''))


# ============================================
# API Routes with Enhanced Debugging
# ============================================

@app.route('/', methods=['GET'])
def home():
    """Home route with API information"""
    return jsonify({
        'message': 'Resume Filter Backend API',
        'version': '1.0',
        'cloudflare_tunnel_url': CLOUDFLARE_TUNNEL_URL,
        'local_url': f'http://localhost:{API_PORT}',
        'filter_available': FILTER_AVAILABLE,
        'tunnel_status': 'active' if CLOUDFLARE_TUNNEL_URL else 'local_only',
        'base_folder': BASE_FOLDER_ABS,
        'endpoints': {
            'get_jobs': f'{CLOUDFLARE_TUNNEL_URL or f"http://localhost:{API_PORT}"}/api/jobs',
            'create_job': f'{CLOUDFLARE_TUNNEL_URL or f"http://localhost:{API_PORT}"}/api/job/create',
            'submit_application': f'{CLOUDFLARE_TUNNEL_URL or f"http://localhost:{API_PORT}"}/api/apply/<ticket_id>',
            'check_health': f'{CLOUDFLARE_TUNNEL_URL or f"http://localhost:{API_PORT}"}/api/health',
            'list_files': f'{CLOUDFLARE_TUNNEL_URL or f"http://localhost:{API_PORT}"}/api/debug/files/<ticket_id>'
        }
    })


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'cloudflare_tunnel_active': CLOUDFLARE_TUNNEL_URL is not None,
        'public_url': CLOUDFLARE_TUNNEL_URL,
        'filter_available': FILTER_AVAILABLE,
        'base_folder_exists': os.path.exists(BASE_FOLDER_ABS),
        'base_folder_path': BASE_FOLDER_ABS,
        'timestamp': datetime.now().isoformat()
    })


# NEW DEBUG ENDPOINT - List files in a ticket folder
@app.route('/api/debug/files/<ticket_id>', methods=['GET'])
def debug_list_files(ticket_id):
    """Debug endpoint to list all files in a ticket folder"""
    ticket_folder = os.path.join(BASE_FOLDER_ABS, ticket_id)
    
    if not os.path.exists(ticket_folder):
        return jsonify({
            'error': 'Ticket folder not found',
            'folder_path': ticket_folder,
            'exists': False
        }), 404
    
    files = []
    for filename in os.listdir(ticket_folder):
        filepath = os.path.join(ticket_folder, filename)
        if os.path.isfile(filepath):
            files.append({
                'filename': filename,
                'size': os.path.getsize(filepath),
                'modified': datetime.fromtimestamp(os.path.getmtime(filepath)).isoformat()
            })
    
    return jsonify({
        'ticket_id': ticket_id,
        'folder_path': ticket_folder,
        'exists': True,
        'files': files,
        'file_count': len(files)
    })


@app.route('/api/apply/<ticket_id>', methods=['POST'])
def submit_application(ticket_id):
    """Submit application with resume - ENHANCED WITH DEBUGGING"""
    logger.info(f"=== STARTING APPLICATION SUBMISSION FOR TICKET: {ticket_id} ===")
    
    # Debug: Print all form data
    logger.info("Form data received:")
    for key, value in request.form.items():
        logger.info(f"  {key}: {value}")
    
    # Debug: Print file information
    logger.info("Files received:")
    for key in request.files:
        file = request.files[key]
        logger.info(f"  {key}: {file.filename} (Content-Type: {file.content_type})")
    
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Verify job exists
        cursor.execute("SELECT * FROM job_tickets WHERE ticket_id = %s AND status = 'posted'", (ticket_id,))
        job = cursor.fetchone()
        
        if not job:
            logger.error(f"Job not found for ticket_id: {ticket_id}")
            return jsonify({'error': 'Job not found or inactive'}), 404
        
        # Check for resume file
        if 'resume' not in request.files:
            logger.error("No 'resume' field in request.files")
            logger.error(f"Available fields: {list(request.files.keys())}")
            return jsonify({'error': 'Resume file is required'}), 400
        
        file = request.files['resume']
        if file.filename == '':
            logger.error("Empty filename")
            return jsonify({'error': 'No file selected'}), 400
        
        logger.info(f"Resume file received: {file.filename}")
        
        if not allowed_file(file.filename):
            logger.error(f"Invalid file type: {file.filename}")
            return jsonify({'error': 'Invalid file type. Only PDF, DOC, DOCX allowed'}), 400
        
        # Get form data
        email = request.form.get('email', '').strip().lower()
        applicant_name = request.form.get('applicant_name', '').strip()
        
        if not email or not applicant_name:
            logger.error(f"Missing required fields - Name: '{applicant_name}', Email: '{email}'")
            return jsonify({'error': 'Name and email are required'}), 400
        
        # Check for duplicate
        cursor.execute(
            "SELECT application_id FROM applications WHERE ticket_id = %s AND email = %s",
            (ticket_id, email)
        )
        if cursor.fetchone():
            logger.error(f"Duplicate application for email: {email}")
            return jsonify({'error': 'You have already applied for this position'}), 409
        
        # Create ticket folder with absolute path
        ticket_folder = os.path.join(BASE_FOLDER_ABS, ticket_id)
        logger.info(f"Creating/checking ticket folder: {ticket_folder}")
        
        try:
            os.makedirs(ticket_folder, exist_ok=True)
            logger.info(f"✅ Ticket folder ensured at: {ticket_folder}")
        except Exception as e:
            logger.error(f"Failed to create ticket folder: {e}")
            return jsonify({'error': f'Failed to create folder: {str(e)}'}), 500
        
        # Generate filename
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = secure_filename(applicant_name.replace(' ', '_'))
        original_filename = secure_filename(file.filename)
        filename = f"{timestamp}_{safe_name}_{original_filename}"
        filepath = os.path.join(ticket_folder, filename)
        
        logger.info(f"Saving file to: {filepath}")
        
        # Save file
        try:
            file.save(filepath)
            logger.info(f"✅ File saved successfully to: {filepath}")
            
            # Verify file was saved
            if os.path.exists(filepath):
                file_size = os.path.getsize(filepath)
                logger.info(f"✅ File verified - Size: {file_size} bytes")
            else:
                logger.error(f"❌ File not found after save: {filepath}")
                return jsonify({'error': 'File save verification failed'}), 500
                
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            logger.error(traceback.format_exc())
            return jsonify({'error': f'Failed to save file: {str(e)}'}), 500
        
        file_size = os.path.getsize(filepath)
        file_hash = get_file_hash(filepath)
        
        logger.info(f"File details - Size: {file_size}, Hash: {file_hash}")
        
        # Extract resume text if filter available
        resume_text = ""
        if FILTER_AVAILABLE:
            try:
                resume_text = ResumeExtractor.extract_text(Path(filepath))
                logger.info(f"✅ Resume text extracted - Length: {len(resume_text)}")
            except Exception as e:
                logger.warning(f"Failed to extract resume text: {e}")
        
        # Save application to database
        insert_query = """
        INSERT INTO applications (
            ticket_id, applicant_name, email, phone, resume_filename, resume_path,
            file_size, file_hash, cover_letter, linkedin_url, portfolio_url,
            github_url, years_of_experience, current_location, expected_salary,
            notice_period, skills, submission_ip
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        values = (
            ticket_id, applicant_name, email,
            request.form.get('phone', ''),
            filename, filepath, file_size, file_hash,
            request.form.get('cover_letter', ''),
            request.form.get('linkedin_url', ''),
            request.form.get('portfolio_url', ''),
            request.form.get('github_url', ''),
            int(request.form.get('years_of_experience', 0)),
            request.form.get('current_location', ''),
            request.form.get('expected_salary', ''),
            request.form.get('notice_period', ''),
            request.form.get('skills', ''),
            request.remote_addr
        )
        
        cursor.execute(insert_query, values)
        application_id = cursor.lastrowid
        
        logger.info(f"✅ Application saved to database - ID: {application_id}")
        
        # Save to resumes table
        resume_query = """
        INSERT INTO resumes (ticket_id, file_path, filename, file_hash, resume_text)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE resume_text = VALUES(resume_text)
        """
        cursor.execute(resume_query, (ticket_id, filepath, filename, file_hash, resume_text))
        
        conn.commit()
        
        # Queue for filtering if available
        if FILTER_AVAILABLE:
            processing_queue.put({
                'ticket_id': ticket_id,
                'application_id': application_id
            })
            logger.info("✅ Queued for AI filtering")
        
        # List all files in the folder for confirmation
        files_in_folder = os.listdir(ticket_folder)
        logger.info(f"Files in ticket folder after save: {files_in_folder}")
        
        return jsonify({
            'success': True,
            'message': 'Application submitted successfully',
            'application_id': application_id,
            'ticket_id': ticket_id,
            'file_saved': {
                'filename': filename,
                'filepath': filepath,
                'size': file_size,
                'folder': ticket_folder,
                'files_in_folder': files_in_folder
            }
        }), 201
        
    except Error as e:
        logger.error(f"Database error: {e}")
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up file: {filepath}")
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Server error: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()
        logger.info(f"=== COMPLETED APPLICATION SUBMISSION FOR TICKET: {ticket_id} ===\n")


# Keep all other routes the same...
@app.route('/api/jobs', methods=['GET'])
def get_jobs():
    """Get all jobs matching frontend data structure"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        query = """
        SELECT 
            ticket_id,
            job_title,
            position,
            location,
            experience_required,
            salary_range,
            employment_type,
            deadline,
            job_description,
            required_skills,
            sender,
            subject,
            status,
            approved_at,
            created_at,
            last_updated,
            updated_after_approval
        FROM job_tickets
        WHERE status = 'posted'
        ORDER BY created_at DESC
        """
        
        cursor.execute(query)
        jobs = cursor.fetchall()
        
        # Format dates
        for job in jobs:
            if job['approved_at']:
                job['approved_at'] = job['approved_at'].strftime('%Y-%m-%dT%H:%M:%S')
            if job['created_at']:
                job['created_at'] = job['created_at'].strftime('%Y-%m-%dT%H:%M:%S')
            if job['last_updated']:
                job['last_updated'] = job['last_updated'].strftime('%Y-%m-%dT%H:%M:%S')
        
        return jsonify({
            'data': {
                'jobs': jobs
            }
        })
        
    except Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/job/create', methods=['POST'])
def create_job():
    """Create a new job ticket"""
    try:
        data = request.get_json()
        
        if not data.get('ticket_id') or not data.get('job_title'):
            return jsonify({'error': 'ticket_id and job_title are required'}), 400
        
        ticket_id = data['ticket_id']
        
        # Create ticket folder with absolute path
        ticket_folder = os.path.join(BASE_FOLDER_ABS, ticket_id)
        if os.path.exists(ticket_folder):
            return jsonify({'error': 'Ticket ID already exists'}), 409
        
        os.makedirs(ticket_folder, exist_ok=True)
        logger.info(f"Created job folder: {ticket_folder}")
        
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'}), 500
        
        try:
            cursor = conn.cursor()
            
            # Prepare job data
            job_data = {
                'ticket_id': ticket_id,
                'job_title': data['job_title'],
                'position': data.get('position', data['job_title']),
                'location': data.get('location', ''),
                'experience_required': data.get('experience_required', ''),
                'salary_range': data.get('salary_range', ''),
                'employment_type': data.get('employment_type', 'Full-time'),
                'deadline': data.get('deadline', 'Open until filled'),
                'job_description': data.get('job_description', ''),
                'required_skills': data.get('required_skills', ''),
                'nice_to_have': data.get('nice_to_have', ''),
                'sender': data.get('sender', 'hr@company.com'),
                'subject': data.get('subject', f"Re: {data['job_title']}"),
                'status': 'posted',
                'approved_at': datetime.now(),
                'approval_status': 'approved',
                'approved': True,
                'updated_after_approval': False,
                'folder_path': ticket_folder
            }
            
            # Insert into database
            insert_query = """
            INSERT INTO job_tickets (
                ticket_id, job_title, position, location, experience_required,
                salary_range, employment_type, deadline, job_description,
                required_skills, nice_to_have, sender, subject, status,
                approved_at, approval_status, approved, updated_after_approval, folder_path
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            values = tuple(job_data.values())
            cursor.execute(insert_query, values)
            
            conn.commit()
            
            # Create job-data.json
            job_data['created_at'] = datetime.now().isoformat()
            job_data['last_updated'] = datetime.now().isoformat()
            create_job_data_json(ticket_folder, job_data)
            
            return jsonify({
                'success': True,
                'message': 'Job ticket created successfully',
                'ticket_id': ticket_id,
                'folder_path': ticket_folder
            }), 201
            
        except Error as e:
            import shutil
            shutil.rmtree(ticket_folder, ignore_errors=True)
            return jsonify({'error': f'Database error: {str(e)}'}), 500
        finally:
            cursor.close()
            conn.close()
            
    except Exception as e:
        return jsonify({'error': f'Server error: {str(e)}'}), 500


# Background worker remains the same
def process_worker():
    """Background worker to process filtering jobs"""
    while True:
        try:
            job = processing_queue.get(timeout=5)
            
            if FILTER_AVAILABLE:
                ticket_id = job['ticket_id']
                print(f"🔄 Running filter for ticket: {ticket_id}")
                
                ticket_folder = os.path.join(BASE_FOLDER_ABS, ticket_id)
                
                try:
                    # Run the filtering system
                    filter_system = UpdatedResumeFilteringSystem(ticket_folder)
                    results = filter_system.filter_resumes()
                    
                    if "error" not in results:
                        print(f"✅ Filtering completed for ticket: {ticket_id}")
                        
                        # Update application statuses
                        conn = get_db_connection()
                        if conn:
                            cursor = conn.cursor()
                            
                            if 'final_top_5' in results:
                                for candidate in results['final_top_5']:
                                    cursor.execute("""
                                        UPDATE applications a
                                        SET a.application_status = 'shortlisted',
                                            a.ai_score = %s
                                        WHERE a.ticket_id = %s 
                                        AND a.resume_filename = %s
                                    """, (
                                        candidate['final_score'],
                                        ticket_id,
                                        candidate['filename']
                                    ))
                            
                            cursor.execute("""
                                UPDATE applications 
                                SET application_status = 'processed',
                                    processed_at = NOW()
                                WHERE ticket_id = %s 
                                AND application_status = 'submitted'
                            """, (ticket_id,))
                            
                            conn.commit()
                            cursor.close()
                            conn.close()
                    else:
                        print(f"❌ Filtering error: {results.get('error')}")
                        
                except Exception as e:
                    print(f"❌ Error running filter: {str(e)}")
                    
        except queue.Empty:
            continue
        except Exception as e:
            print(f"Worker error: {str(e)}")


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
# Main Entry Point
# ============================================

if __name__ == '__main__':
    import socket
    
    # Get local IP address
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print("\n" + "="*60)
    print("🚀 RESUME FILTER BACKEND WITH CLOUDFLARE TUNNEL")
    print("="*60)
    print(f"📁 Base folder: {BASE_FOLDER_ABS}")
    
    # Setup database
    print("\n📊 Setting up database...")
    if not setup_database():
        print("❌ Failed to setup database. Exiting...")
        sys.exit(1)
    
    # Start background worker
    if FILTER_AVAILABLE:
        worker_thread = threading.Thread(target=process_worker, daemon=True)
        worker_thread.start()
        print("✅ Background filtering worker started")
    else:
        print("⚠️ Resume filter not available. Background worker disabled.")
    
    # Start Cloudflare tunnel
    print("\n🌐 Starting Cloudflare tunnel...")
    public_url = start_cloudflare_tunnel()
    
    if public_url:
        print(f"\n✅ Your API is now accessible worldwide at:")
        print(f"   {public_url}")
        print(f"\n📱 Configure your frontend to use:")
        print(f"   const API_URL = '{public_url}/api';")
        print(f"\n📚 Example API calls:")
        print(f"   GET {public_url}/api/jobs")
        print(f"   POST {public_url}/api/apply/{{ticket_id}}")
        print(f"   GET {public_url}/api/debug/files/{{ticket_id}}")
    else:
        print(f"\n⚠️  Running in local mode only")
        print(f"   API available at: http://localhost:{API_PORT}")
        print("   Install cloudflared for public access")
    
    print("\n" + "="*60)
    print("📌 Server starting...")
    print("✋ Press CTRL+C to stop")
    print("="*60 + "\n")
    
    try:
        # Run the Flask server
        # Important: debug=False for production
        app.run(host='0.0.0.0', port=API_PORT, debug=False)
    except KeyboardInterrupt:
        cleanup_on_exit()
    except Exception as e:
        print(f"❌ Server error: {e}")
        cleanup_on_exit()