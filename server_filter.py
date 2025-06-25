# Complete Flask Backend with Integrated Ngrok Support
# backend_with_ngrok.py

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
from pyngrok import ngrok
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

# CORS configuration for ngrok
CORS(app, 
     origins="*",  # Allow all origins for ngrok
     allow_headers="*",
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     supports_credentials=True)

# Configuration
BASE_FOLDER = 'jobs-data'
ALLOWED_EXTENSIONS = {'pdf', 'doc', 'docx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

app.config['BASE_FOLDER'] = BASE_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# Create base folder
os.makedirs(BASE_FOLDER, exist_ok=True)

# Database configuration - CHANGE THESE VALUES TO MATCH YOUR SETUP
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Khan@123',  # Add your MySQL password here
    'database': 'resume_filter_db'
}

# Ngrok configuration (optional - leave empty for free tier)
NGROK_AUTH_TOKEN = '2ymLAvkIdITS8prp9D2GuVCzWGT_25adn7rMKQBFu5WgJke45'  # Add your ngrok auth token here if you have one

# Processing queue
processing_queue = queue.Queue()
filtering_status = {}

# Global variable to store ngrok URL
NGROK_URL = None


def start_ngrok():
    """Start ngrok tunnel and return public URL"""
    global NGROK_URL
    try:
        # Kill any existing ngrok processes
        ngrok.kill()
        
        # Set auth token if available
        if NGROK_AUTH_TOKEN:
            ngrok.set_auth_token(NGROK_AUTH_TOKEN)
            print("✅ Ngrok auth token configured")
        else:
            print("⚠️ No NGROK_AUTH_TOKEN configured. Using free tier.")
        
        # Start ngrok tunnel on port 5000
        tunnel = ngrok.connect(5000, "http")
        NGROK_URL = tunnel.public_url
        
        print("\n" + "="*60)
        print("🌐 NGROK TUNNEL ACTIVE")
        print("="*60)
        print(f"📱 Public URL: {NGROK_URL}")
        print(f"🔗 Share this URL with your frontend: {NGROK_URL}")
        print(f"📊 Ngrok Web Interface: http://127.0.0.1:4040")
        print(f"⚠️  This URL will change when you restart the server")
        print("="*60 + "\n")
        
        return NGROK_URL
    except Exception as e:
        print(f"\n❌ Ngrok setup failed: {e}")
        print("📍 API will only be available locally at http://localhost:5000")
        print("💡 To use ngrok, install it: pip install pyngrok")
        print("💡 Get free auth token at: https://dashboard.ngrok.com/auth")
        return None


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
        
        # Job skills table
        job_skills_table = """
        CREATE TABLE IF NOT EXISTS job_skills (
            id INT AUTO_INCREMENT PRIMARY KEY,
            ticket_id VARCHAR(255),
            skill VARCHAR(255),
            skill_type ENUM('required', 'nice_to_have') DEFAULT 'required',
            FOREIGN KEY (ticket_id) REFERENCES job_tickets(ticket_id) ON DELETE CASCADE,
            INDEX idx_ticket_skill (ticket_id, skill)
        )
        """
        cursor.execute(job_skills_table)
        
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
        
        # Final selections table (with backticks for rank)
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


# API Routes

@app.route('/', methods=['GET'])
def home():
    """Home route with API information"""
    return jsonify({
        'message': 'Resume Filter Backend API',
        'version': '1.0',
        'ngrok_url': NGROK_URL,
        'local_url': 'http://localhost:5000',
        'filter_available': FILTER_AVAILABLE,
        'endpoints': {
            'get_jobs': f'{NGROK_URL or "http://localhost:5000"}/api/jobs',
            'create_job': f'{NGROK_URL or "http://localhost:5000"}/api/job/create',
            'submit_application': f'{NGROK_URL or "http://localhost:5000"}/api/apply/<ticket_id>',
            'check_health': f'{NGROK_URL or "http://localhost:5000"}/api/health'
        }
    })


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'ngrok_active': NGROK_URL is not None,
        'public_url': NGROK_URL,
        'filter_available': FILTER_AVAILABLE,
        'timestamp': datetime.now().isoformat()
    })


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
        
        # Create ticket folder
        ticket_folder = os.path.join(app.config['BASE_FOLDER'], ticket_id)
        if os.path.exists(ticket_folder):
            return jsonify({'error': 'Ticket ID already exists'}), 409
        
        os.makedirs(ticket_folder, exist_ok=True)
        
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


@app.route('/api/apply/<ticket_id>', methods=['POST'])
def submit_application(ticket_id):
    """Submit application with resume"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Verify job exists
        cursor.execute("SELECT * FROM job_tickets WHERE ticket_id = %s AND status = 'posted'", (ticket_id,))
        job = cursor.fetchone()
        
        if not job:
            return jsonify({'error': 'Job not found or inactive'}), 404
        
        # Check for resume file
        if 'resume' not in request.files:
            return jsonify({'error': 'Resume file is required'}), 400
        
        file = request.files['resume']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Invalid file type. Only PDF, DOC, DOCX allowed'}), 400
        
        # Get form data
        email = request.form.get('email', '').strip().lower()
        applicant_name = request.form.get('applicant_name', '').strip()
        
        if not email or not applicant_name:
            return jsonify({'error': 'Name and email are required'}), 400
        
        # Check for duplicate
        cursor.execute(
            "SELECT application_id FROM applications WHERE ticket_id = %s AND email = %s",
            (ticket_id, email)
        )
        if cursor.fetchone():
            return jsonify({'error': 'You have already applied for this position'}), 409
        
        # Save file
        ticket_folder = os.path.join(app.config['BASE_FOLDER'], ticket_id)
        os.makedirs(ticket_folder, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = secure_filename(applicant_name.replace(' ', '_'))
        filename = f"{timestamp}_{safe_name}_{secure_filename(file.filename)}"
        filepath = os.path.join(ticket_folder, filename)
        
        file.save(filepath)
        file_size = os.path.getsize(filepath)
        file_hash = get_file_hash(filepath)
        
        # Extract resume text if filter available
        resume_text = ""
        if FILTER_AVAILABLE:
            try:
                resume_text = ResumeExtractor.extract_text(Path(filepath))
            except:
                pass
        
        # Save application
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
        
        return jsonify({
            'success': True,
            'message': 'Application submitted successfully',
            'application_id': application_id,
            'ticket_id': ticket_id
        }), 201
        
    except Error as e:
        if 'filepath' in locals() and os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()


@app.route('/api/filter/<ticket_id>', methods=['POST'])
def trigger_filtering(ticket_id):
    """Manually trigger resume filtering"""
    if not FILTER_AVAILABLE:
        return jsonify({'error': 'Resume filter not available'}), 503
    
    processing_queue.put({
        'ticket_id': ticket_id,
        'manual_trigger': True
    })
    
    return jsonify({
        'success': True,
        'message': 'Filtering process started',
        'ticket_id': ticket_id
    })


@app.route('/api/results/<ticket_id>', methods=['GET'])
def get_filtering_results(ticket_id):
    """Get filtering results"""
    conn = get_db_connection()
    if not conn:
        return jsonify({'error': 'Database connection failed'}), 500
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        query = """
        SELECT 
            fs.`rank`,
            fs.selection_reason,
            r.filename,
            rs.final_score,
            rs.skill_score,
            rs.experience_score,
            rs.matched_skills,
            rs.detected_experience_years,
            a.applicant_name,
            a.email,
            a.phone
        FROM final_selections fs
        JOIN resumes r ON fs.resume_id = r.resume_id
        JOIN resume_scores rs ON r.resume_id = rs.resume_id
        LEFT JOIN applications a ON r.file_hash = a.file_hash AND r.ticket_id = a.ticket_id
        WHERE fs.ticket_id = %s
        ORDER BY fs.`rank`
        """
        
        cursor.execute(query, (ticket_id,))
        results = cursor.fetchall()
        
        formatted_results = []
        for res in results:
            formatted_results.append({
                'rank': res['rank'],
                'name': res['applicant_name'] or res['filename'],
                'email': res['email'],
                'phone': res['phone'],
                'filename': res['filename'],
                'scores': {
                    'final': float(res['final_score']) if res['final_score'] else 0,
                    'skills': float(res['skill_score']) if res['skill_score'] else 0,
                    'experience': float(res['experience_score']) if res['experience_score'] else 0
                },
                'matched_skills': json.loads(res['matched_skills']) if res['matched_skills'] else [],
                'experience_years': res['detected_experience_years'],
                'selection_reason': res['selection_reason']
            })
        
        return jsonify({
            'success': True,
            'ticket_id': ticket_id,
            'top_candidates': formatted_results
        })
        
    except Error as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500
    finally:
        cursor.close()
        conn.close()


# Background worker for filtering
def process_worker():
    """Background worker to process filtering jobs"""
    while True:
        try:
            job = processing_queue.get(timeout=5)
            
            if FILTER_AVAILABLE:
                ticket_id = job['ticket_id']
                print(f"🔄 Running filter for ticket: {ticket_id}")
                
                ticket_folder = os.path.join(BASE_FOLDER, ticket_id)
                
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


if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 RESUME FILTER BACKEND WITH NGROK")
    print("="*60)
    
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
    
    # Start ngrok
    print("\n🌐 Starting ngrok tunnel...")
    public_url = start_ngrok()
    
    if public_url:
        print(f"\n✅ Your API is now accessible worldwide at:")
        print(f"   {public_url}")
        print(f"\n📱 Configure your frontend to use:")
        print(f"   const API_URL = '{public_url}/api';")
    else:
        print(f"\n📍 API running locally at:")
        print(f"   http://localhost:5000")
    
    print("\n" + "="*60)
    print("📌 Server starting...")
    print("="*60 + "\n")
    
    # Run the Flask server
    # Important: debug=False for ngrok to work properly
    app.run(host='0.0.0.0', port=5000, debug=False)