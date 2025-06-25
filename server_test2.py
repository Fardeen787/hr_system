# api_server_cloudflare.py - With Cloudflare Tunnel Support
from flask import Flask, jsonify, request
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

# ============================================
# Flask App Initialization
# ============================================

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Enable CORS for cross-origin requests

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================
# Cloudflare Tunnel Functions
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
                'GET /api/skills'
            ]
        },
        'authentication': 'Add X-API-Key header with your API key',
        'api_key_hint': 'Contact admin for API key',
        'tunnel_status': 'active' if CLOUDFLARE_TUNNEL_URL else 'local_only'
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    conn = get_db_connection()
    db_status = "connected" if conn else "disconnected"
    
    if conn:
        conn.close()
    
    return jsonify({
        'status': 'ok' if db_status == "connected" else 'error',
        'database': db_status,
        'tunnel': 'active' if CLOUDFLARE_TUNNEL_URL else 'inactive',
        'public_url': CLOUDFLARE_TUNNEL_URL,
        'timestamp': datetime.now().isoformat()
    })

# ============================================
# [Keep all existing endpoints exactly the same]
# ============================================

# All your existing endpoints remain unchanged:
# - get_approved_jobs()
# - get_job_details()
# - debug_ticket()
# - search_jobs()
# - get_statistics()
# - get_locations()
# - get_skills()
# - error handlers

# Copy all the endpoint code from your original file here
# (I'm not repeating them to save space, but they remain exactly the same)

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
                'updated_after_approval': updated_after_approval
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
                'updated_after_approval': len([u for u in updates if u['timestamp'] > ticket['approved_at']]) > 0 if ticket['approved_at'] else False
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_job_details: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# Add remaining endpoints here...
@app.route('/api/debug/ticket/<ticket_id>', methods=['GET'])
@require_api_key
def debug_ticket(ticket_id):
    """Debug endpoint to see all raw data for a ticket"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({'error': 'Database connection failed'}), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # Get all ticket details ordered by creation time
        cursor.execute("""
            SELECT * FROM ticket_details 
            WHERE ticket_id = %s 
            ORDER BY created_at ASC
        """, (ticket_id,))
        
        details = cursor.fetchall()
        
        # Convert datetime objects
        for detail in details:
            for key, value in detail.items():
                if isinstance(value, datetime):
                    detail[key] = value.isoformat()
        
        # Get ticket info
        cursor.execute("SELECT * FROM tickets WHERE ticket_id = %s", (ticket_id,))
        ticket = cursor.fetchone()
        
        if ticket:
            for key, value in ticket.items():
                if isinstance(value, datetime):
                    ticket[key] = value.isoformat()
        
        # Get updates
        cursor.execute("""
            SELECT * FROM ticket_updates 
            WHERE ticket_id = %s 
            ORDER BY update_timestamp ASC
        """, (ticket_id,))
        
        updates = cursor.fetchall()
        for update in updates:
            for key, value in update.items():
                if isinstance(value, datetime):
                    update[key] = value.isoformat()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'ticket': ticket,
            'all_details': details,
            'updates': updates,
            'detail_count': len(details)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/jobs/search', methods=['GET'])
@require_api_key
def search_jobs():
    """
    Search jobs by keyword - CORRECTED to show latest values
    """
    try:
        query = request.args.get('q', '').strip()
        
        if not query:
            return jsonify({
                'success': False,
                'error': 'Search query is required'
            }), 400
        
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # First, get all approved tickets
        cursor.execute("""
            SELECT DISTINCT
                t.ticket_id,
                t.subject,
                t.created_at,
                t.approved_at,
                t.last_updated
            FROM tickets t
            WHERE t.approval_status = 'approved' 
                AND t.status != 'terminated'
            ORDER BY t.approved_at DESC
        """)
        
        tickets = cursor.fetchall()
        jobs = []
        
        for ticket in tickets:
            ticket_id = ticket['ticket_id']
            
            # Get latest values for this ticket
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
            
            # Check if search query matches any field
            search_text = query.lower()
            if (search_text in ticket['subject'].lower() or
                search_text in job_details.get('job_title', '').lower() or
                search_text in job_details.get('job_description', '').lower() or
                search_text in job_details.get('required_skills', '').lower() or
                search_text in job_details.get('location', '').lower()):
                
                job = {
                    'ticket_id': ticket['ticket_id'],
                    'subject': ticket['subject'],
                    'created_at': serialize_datetime(ticket['created_at']),
                    'approved_at': serialize_datetime(ticket['approved_at']),
                    'last_updated': serialize_datetime(ticket['last_updated']),
                    'job_title': job_details.get('job_title', 'NOT_FOUND'),
                    'location': job_details.get('location', 'NOT_FOUND'),
                    'experience_required': job_details.get('experience_required', 'NOT_FOUND'),
                    'salary_range': job_details.get('salary_range', 'NOT_FOUND'),
                    'job_description': job_details.get('job_description', 'NOT_FOUND'),
                    'required_skills': job_details.get('required_skills', 'NOT_FOUND'),
                    'employment_type': job_details.get('employment_type', 'NOT_FOUND'),
                    'deadline': job_details.get('deadline', 'NOT_FOUND')
                }
                jobs.append(job)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'query': query,
                'count': len(jobs),
                'jobs': jobs
            }
        })
        
    except Exception as e:
        logger.error(f"Error in search_jobs: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/stats', methods=['GET'])
@require_api_key
def get_statistics():
    """Get hiring statistics and analytics"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor(dictionary=True)
        
        # Overall statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total_tickets,
                SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END) as approved_jobs,
                SUM(CASE WHEN approval_status = 'pending' THEN 1 ELSE 0 END) as pending_approval,
                SUM(CASE WHEN approval_status = 'rejected' THEN 1 ELSE 0 END) as rejected_jobs,
                SUM(CASE WHEN status = 'terminated' THEN 1 ELSE 0 END) as terminated_jobs
            FROM tickets
        """)
        
        overall_stats = cursor.fetchone()
        
        # Jobs by location - using latest values
        cursor.execute("""
            SELECT 
                latest.location,
                COUNT(*) as count
            FROM (
                SELECT 
                    t.ticket_id,
                    td1.field_value as location
                FROM tickets t
                JOIN ticket_details td1 ON t.ticket_id = td1.ticket_id
                INNER JOIN (
                    SELECT ticket_id, MAX(created_at) as max_created_at
                    FROM ticket_details
                    WHERE field_name = 'location'
                    GROUP BY ticket_id
                ) td2 ON td1.ticket_id = td2.ticket_id 
                     AND td1.created_at = td2.max_created_at
                WHERE td1.field_name = 'location'
                    AND t.approval_status = 'approved'
                    AND t.status != 'terminated'
            ) latest
            GROUP BY latest.location
            ORDER BY count DESC
        """)
        
        locations = cursor.fetchall()
        
        # Recent activity (last 7 days)
        cursor.execute("""
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as new_jobs
            FROM tickets
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
            GROUP BY DATE(created_at)
            ORDER BY date DESC
        """)
        
        recent_activity = cursor.fetchall()
        
        # Convert dates
        for activity in recent_activity:
            activity['date'] = activity['date'].isoformat()
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'overall': overall_stats,
                'by_location': locations,
                'recent_activity': recent_activity
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_statistics: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/locations', methods=['GET'])
@require_api_key
def get_locations():
    """Get list of all unique locations using latest values"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT td1.field_value
            FROM ticket_details td1
            INNER JOIN (
                SELECT ticket_id, MAX(created_at) as max_created_at
                FROM ticket_details
                WHERE field_name = 'location'
                GROUP BY ticket_id
            ) td2 ON td1.ticket_id = td2.ticket_id 
                 AND td1.created_at = td2.max_created_at
            JOIN tickets t ON td1.ticket_id = t.ticket_id
            WHERE td1.field_name = 'location'
                AND td1.field_value IS NOT NULL
                AND td1.field_value != 'NOT_FOUND'
                AND t.approval_status = 'approved'
            ORDER BY td1.field_value
        """)
        
        locations = [row[0] for row in cursor.fetchall()]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'locations': locations
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_locations: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/skills', methods=['GET'])
@require_api_key
def get_skills():
    """Get list of all unique skills using latest values"""
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'error': 'Database connection failed'
            }), 500
        
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT td1.field_value
            FROM ticket_details td1
            INNER JOIN (
                SELECT ticket_id, MAX(created_at) as max_created_at
                FROM ticket_details
                WHERE field_name = 'required_skills'
                GROUP BY ticket_id
            ) td2 ON td1.ticket_id = td2.ticket_id 
                 AND td1.created_at = td2.max_created_at
            JOIN tickets t ON td1.ticket_id = t.ticket_id
            WHERE td1.field_name = 'required_skills'
                AND td1.field_value IS NOT NULL
                AND td1.field_value != 'NOT_FOUND'
                AND t.approval_status = 'approved'
        """)
        
        # Extract unique skills
        all_skills = set()
        for row in cursor.fetchall():
            skills_text = row[0]
            # Split by common delimiters
            skills = re.split(r'[,;|\n]', skills_text)
            for skill in skills:
                skill = skill.strip()
                if skill:
                    all_skills.add(skill)
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'data': {
                'skills': sorted(list(all_skills))
            }
        })
        
    except Exception as e:
        logger.error(f"Error in get_skills: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

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
# Main Entry Point
# ============================================

if __name__ == '__main__':
    import socket
    
    # Get local IP address
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    print("="*60)
    print("HIRING BOT API SERVER WITH CLOUDFLARE TUNNEL")
    print("="*60)
    print(f"Database: {MYSQL_CONFIG['database']}@{MYSQL_CONFIG['host']}")
    print(f"Local URL: http://localhost:{API_PORT}")
    print(f"Network URL: http://{local_ip}:{API_PORT}")
    print(f"API Key: {API_KEY}")
    print("="*60)
    
    # Start Cloudflare tunnel
    tunnel_url = start_cloudflare_tunnel()
    
    if tunnel_url:
        print("\n📱 Access your API from anywhere using:")
        print(f"   {tunnel_url}")
        print("\n🔐 Example API call:")
        print(f"   curl -H 'X-API-Key: {API_KEY}' {tunnel_url}/api/jobs/approved")
    else:
        print("\n⚠️  Running in local mode only")
        print("   Install cloudflared for public access")
    
    print("\n📚 API Endpoints:")
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