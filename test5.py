#!/usr/bin/env python3
"""
Email Hiring Bot using AutoGen Framework with Groq API and MySQL Database
Multi-agent system for processing hiring emails with ticket management
ENHANCED: Uses MySQL database instead of JSON files for data persistence
"""

import imaplib
import email
import os
import smtplib
from email.message import EmailMessage
import tempfile
import sys
import re
import json
from datetime import datetime
import hashlib
from typing import Dict, List, Tuple, Optional, Any
import autogen
from autogen import AssistantAgent, UserProxyAgent, ConversableAgent, GroupChat, GroupChatManager
import logging
import requests
import secrets
import string
import mysql.connector
from mysql.connector import Error
from contextlib import contextmanager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION - UPDATE THESE WITH YOUR CREDENTIALS
# ============================================================================

# EMAIL CREDENTIALS
EMAIL_ADDRESS = "fardeen78754@gmail.com"      # Replace with your email address
EMAIL_PASSWORD = "qfadfftaihyrfysu"           # Replace with your Gmail App Password

# GROQ API CONFIGURATION
GROQ_API_KEY = "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"
GROQ_MODEL = "llama-3.3-70b-versatile"  # Groq's Llama 3.3 70B model
GROQ_API_BASE = "https://api.groq.com/openai/v1"  # Groq endpoint

# EMAIL SERVER SETTINGS (Default for Gmail)
IMAP_SERVER = "imap.gmail.com"                # Gmail IMAP server
SMTP_SERVER = "smtp.gmail.com"                # Gmail SMTP server  
SMTP_PORT = 587                               # Gmail SMTP port

# PROCESSING SETTINGS
MAX_EMAILS_TO_PROCESS = 10                    # How many unread emails to process at once
PROCESS_ONLY_HIRING_EMAILS = True             # Set to False to process all emails

# MySQL DATABASE CONFIGURATION - UPDATE THESE!
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',              # Replace with your MySQL user
    'password': 'Khan@123',  # Replace with your MySQL password
    'database': 'hiring_bot',     # Database name (will be created)
}

# AutoGen Configuration for Groq
config_list = [
    {
        "model": GROQ_MODEL,
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_API_BASE,
        "api_type": "openai",  # Groq uses OpenAI-compatible API
    }
]

llm_config = {
    "config_list": config_list,
    "temperature": 0.1,
    "seed": 42,
    "cache_seed": None,  # Disable caching for dynamic email processing
    "timeout": 120,
    "max_tokens": 500,  # Reduced from default to avoid token limit issues
}

# Required details for hiring emails
REQUIRED_HIRING_DETAILS = [
    "job_title", "location", "experience_required", "salary_range",
    "job_description", "required_skills", "employment_type", "deadline"
]

# ============================================================================
# DATABASE SETUP
# ============================================================================

class DatabaseManager:
    """Manages MySQL database connections and operations"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.setup_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = None
        try:
            conn = mysql.connector.connect(**self.config)
            yield conn
        except Error as e:
            logger.error(f"Database error: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn and conn.is_connected():
                conn.close()
    
    def setup_database(self):
        """Create database and tables if they don't exist"""
        # First, connect without specifying database
        config_without_db = self.config.copy()
        db_name = config_without_db.pop('database')
        
        try:
            conn = mysql.connector.connect(**config_without_db)
            cursor = conn.cursor()
            
            # Create database if it doesn't exist
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
            cursor.execute(f"USE {db_name}")
            
            # Create tickets table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id VARCHAR(10) PRIMARY KEY,
                    sender VARCHAR(255) NOT NULL,
                    subject TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    status VARCHAR(50) DEFAULT 'new',
                    approval_status VARCHAR(50) DEFAULT 'pending',
                    approved BOOLEAN DEFAULT FALSE,
                    approved_at DATETIME,
                    approval_token VARCHAR(32),
                    terminated_at DATETIME,
                    terminated_by VARCHAR(255),
                    termination_reason TEXT,
                    rejected_at DATETIME,
                    rejection_reason TEXT,
                    INDEX idx_sender (sender),
                    INDEX idx_status (status),
                    INDEX idx_approval_status (approval_status)
                )
            """)
            
            # Create ticket_details table for storing job details
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticket_details (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    ticket_id VARCHAR(10) NOT NULL,
                    field_name VARCHAR(100) NOT NULL,
                    field_value TEXT,
                    is_initial BOOLEAN DEFAULT TRUE,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE,
                    INDEX idx_ticket_field (ticket_id, field_name)
                )
            """)
            
            # Create ticket_updates table for tracking updates
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticket_updates (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    ticket_id VARCHAR(10) NOT NULL,
                    update_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_fields JSON,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE,
                    INDEX idx_ticket_updates (ticket_id)
                )
            """)
            
            # Create pending_approvals table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    approval_token VARCHAR(32) PRIMARY KEY,
                    ticket_id VARCHAR(10) NOT NULL,
                    hr_email VARCHAR(255) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    status VARCHAR(50) DEFAULT 'pending',
                    approved_at DATETIME,
                    rejected_at DATETIME,
                    rejection_reason TEXT,
                    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE,
                    INDEX idx_ticket_approval (ticket_id),
                    INDEX idx_status_approval (status)
                )
            """)
            
            conn.commit()
            logger.info("Database and tables created successfully")
            
        except Error as e:
            logger.error(f"Error setting up database: {e}")
            raise
        finally:
            if conn and conn.is_connected():
                cursor.close()
                conn.close()

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def extract_json_from_text(text):
    """Extract JSON from text that might contain markdown code blocks"""
    if not isinstance(text, str):
        return None
    
    # Try direct parsing first
    try:
        return json.loads(text.strip())
    except:
        pass
    
    # Extract from code blocks
    if '```' in text:
        parts = text.split('```')
        for i in range(1, len(parts), 2):  # Odd indices contain code block content
            content = parts[i]
            # Remove language identifier if present
            lines = content.split('\n')
            if lines and lines[0].strip() in ['json', 'JSON', '']:
                content = '\n'.join(lines[1:])
            try:
                return json.loads(content.strip())
            except:
                continue
    
    # Find JSON by braces
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except:
            pass
    
    return None

def clean_response_text(text):
    """Remove code blocks and clean response text"""
    if not isinstance(text, str):
        return str(text) if text else ""
    
    # Remove code blocks
    if '```' in text:
        parts = text.split('```')
        # Keep only parts outside code blocks (even indices)
        cleaned = ''.join(parts[i] for i in range(0, len(parts), 2))
        text = cleaned.strip()
    
    # Remove any JSON that might be in the response
    if text.strip().startswith('{') and text.strip().endswith('}'):
        # This might be JSON instead of text
        try:
            json_data = json.loads(text)
            # If it's JSON, try to extract a message or content field
            if 'message' in json_data:
                text = json_data['message']
            elif 'content' in json_data:
                text = json_data['content']
            elif 'response' in json_data:
                text = json_data['response']
            else:
                # If no text field, convert back to string
                text = str(json_data)
        except:
            pass
    
    return text.strip()

# ============================================================================
# APPROVAL MANAGER WITH MYSQL
# ============================================================================

class ApprovalManager:
    """Manages job posting approvals using MySQL"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def generate_approval_token(self) -> str:
        """Generate a unique approval token"""
        return ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    
    def create_approval_request(self, ticket_id: str, ticket_data: Dict[str, Any], 
                              hr_email: str) -> str:
        """Create a new approval request"""
        approval_token = self.generate_approval_token()
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO pending_approvals (approval_token, ticket_id, hr_email)
                VALUES (%s, %s, %s)
            """, (approval_token, ticket_id, hr_email))
            conn.commit()
        
        logger.info(f"Created approval request with token: {approval_token}")
        return approval_token
    
    def process_approval(self, token: str) -> Tuple[bool, str, Optional[str]]:
        """Process an approval token. Returns (success, message, ticket_id)"""
        logger.info(f"Processing approval for token: {token}")
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Check if token exists and is pending
            cursor.execute("""
                SELECT ticket_id, status FROM pending_approvals 
                WHERE approval_token = %s
            """, (token,))
            
            approval = cursor.fetchone()
            
            if not approval:
                logger.warning(f"Token {token} not found in approvals")
                return False, "Invalid approval token", None
            
            if approval['status'] != 'pending':
                logger.warning(f"Approval already processed with status: {approval['status']}")
                return False, f"Approval already processed (status: {approval['status']})", approval['ticket_id']
            
            # Update approval status
            cursor.execute("""
                UPDATE pending_approvals 
                SET status = 'approved', approved_at = NOW()
                WHERE approval_token = %s
            """, (token,))
            
            conn.commit()
            
            logger.info(f"Approval processed successfully for ticket: {approval['ticket_id']}")
            return True, "Approval processed successfully", approval['ticket_id']
    
    def process_rejection(self, token: str, reason: str) -> Tuple[bool, str, Optional[str]]:
        """Process a rejection. Returns (success, message, ticket_id)"""
        logger.info(f"Processing rejection for token: {token}")
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Check if token exists and is pending
            cursor.execute("""
                SELECT ticket_id, status FROM pending_approvals 
                WHERE approval_token = %s
            """, (token,))
            
            approval = cursor.fetchone()
            
            if not approval:
                return False, "Invalid approval token", None
            
            if approval['status'] != 'pending':
                return False, f"Approval already processed (status: {approval['status']})", approval['ticket_id']
            
            # Update approval status
            cursor.execute("""
                UPDATE pending_approvals 
                SET status = 'rejected', rejected_at = NOW(), rejection_reason = %s
                WHERE approval_token = %s
            """, (reason, token))
            
            conn.commit()
            
            logger.info(f"Job posting rejected for ticket: {approval['ticket_id']}")
            return True, f"Job posting rejected: {reason}", approval['ticket_id']

# ============================================================================
# ENHANCED TICKET MANAGEMENT SYSTEM WITH MYSQL
# ============================================================================

class TicketManager:
    """Manages hiring tickets with MySQL persistence"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def generate_ticket_id(self, sender: str, subject: str) -> str:
        """Generate a unique ticket ID based on sender and subject"""
        # Clean subject for consistent ticket ID generation
        cleaned_subject = subject.lower()
        prefixes_to_remove = ['re:', 'fwd:', 'update on', 'updated', 'update:', 'modification', 'change']
        for prefix in prefixes_to_remove:
            if cleaned_subject.startswith(prefix):
                cleaned_subject = cleaned_subject[len(prefix):].strip()
        
        # Also remove "position" suffix variations
        suffixes_to_remove = ['position', 'role', 'opening', 'job']
        for suffix in suffixes_to_remove:
            if cleaned_subject.endswith(suffix):
                cleaned_subject = cleaned_subject[:-len(suffix)].strip()
        
        hash_input = f"{sender}_{cleaned_subject}".lower()
        return hashlib.md5(hash_input.encode()).hexdigest()[:10]
    
    def create_or_update_ticket_with_id(self, ticket_id: str, sender: str, subject: str, 
                                       details: Dict[str, str], timestamp: str) -> Tuple[str, bool, str]:
        """Update an existing ticket with a specific ticket ID"""
        logger.info(f"create_or_update_ticket_with_id called with ticket_id: {ticket_id}")
        logger.info(f"Details to save: {details}")
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Check if ticket exists
            cursor.execute("""
                SELECT status, approval_status FROM tickets 
                WHERE ticket_id = %s
            """, (ticket_id,))
            
            existing = cursor.fetchone()
            
            if not existing:
                logger.error(f"Ticket {ticket_id} not found for update!")
                return ticket_id, False, "not_found"
            
            if existing['status'] == 'terminated':
                return ticket_id, False, "terminated"
            
            if existing['approval_status'] == 'approved':
                logger.warning(f"Ticket {ticket_id} is already approved and cannot be updated")
                return ticket_id, False, "approved_locked"
            
            # Update ticket
            cursor.execute("""
                UPDATE tickets 
                SET last_updated = NOW(), status = 'updated'
                WHERE ticket_id = %s
            """, (ticket_id,))
            
            # Insert update record
            cursor.execute("""
                INSERT INTO ticket_updates (ticket_id, updated_fields)
                VALUES (%s, %s)
            """, (ticket_id, json.dumps(details)))
            
            # Update ticket details
            for field_name, field_value in details.items():
                if field_value and field_value != "NOT_FOUND":
                    cursor.execute("""
                        INSERT INTO ticket_details (ticket_id, field_name, field_value, is_initial)
                        VALUES (%s, %s, %s, FALSE)
                    """, (ticket_id, field_name, field_value))
            
            conn.commit()
            
            logger.info(f"Ticket {ticket_id} updated successfully")
            return ticket_id, True, "active"
    
    def create_or_update_ticket(self, sender: str, subject: str, details: Dict[str, str], 
                                timestamp: str) -> Tuple[str, bool, str]:
        """Create a new ticket or update existing one"""
        ticket_id = self.generate_ticket_id(sender, subject)
        
        logger.info(f"create_or_update_ticket called with ticket_id: {ticket_id}")
        logger.info(f"Details to save: {details}")
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Check if ticket exists
            cursor.execute("""
                SELECT status, approval_status FROM tickets 
                WHERE ticket_id = %s
            """, (ticket_id,))
            
            existing = cursor.fetchone()
            
            if existing:
                if existing['status'] == 'terminated':
                    return ticket_id, False, "terminated"
                
                if existing['approval_status'] == 'approved':
                    logger.warning(f"Ticket {ticket_id} is already approved and cannot be updated")
                    return ticket_id, False, "approved_locked"
                
                # Update existing ticket
                cursor.execute("""
                    UPDATE tickets 
                    SET last_updated = NOW(), status = 'updated'
                    WHERE ticket_id = %s
                """, (ticket_id,))
                
                # Insert update record
                cursor.execute("""
                    INSERT INTO ticket_updates (ticket_id, updated_fields)
                    VALUES (%s, %s)
                """, (ticket_id, json.dumps(details)))
                
                # Update ticket details
                for field_name, field_value in details.items():
                    if field_value and field_value != "NOT_FOUND":
                        cursor.execute("""
                            INSERT INTO ticket_details (ticket_id, field_name, field_value, is_initial)
                            VALUES (%s, %s, %s, FALSE)
                        """, (ticket_id, field_name, field_value))
                
                is_update = True
            else:
                # Create new ticket
                cursor.execute("""
                    INSERT INTO tickets (ticket_id, sender, subject)
                    VALUES (%s, %s, %s)
                """, (ticket_id, sender, subject))
                
                # Insert initial details
                for field_name, field_value in details.items():
                    if field_value and field_value != "NOT_FOUND":
                        cursor.execute("""
                            INSERT INTO ticket_details (ticket_id, field_name, field_value, is_initial)
                            VALUES (%s, %s, %s, TRUE)
                        """, (ticket_id, field_name, field_value))
                
                is_update = False
            
            conn.commit()
            
            return ticket_id, is_update, "active"
    
    def approve_ticket(self, ticket_id: str) -> bool:
        """Approve a ticket and mark it as ready for website display"""
        logger.info(f"Approving ticket: {ticket_id}")
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE tickets 
                SET approval_status = 'approved', 
                    approved = TRUE, 
                    approved_at = NOW(),
                    status = 'posted'
                WHERE ticket_id = %s
            """, (ticket_id,))
            
            affected = cursor.rowcount
            conn.commit()
            
            if affected > 0:
                logger.info(f"Ticket {ticket_id} approved and marked as posted")
                return True
            else:
                logger.error(f"Failed to approve ticket {ticket_id}")
                return False
    
    def terminate_ticket(self, ticket_id: str, terminated_by: str, reason: str = "") -> bool:
        """Terminate a ticket"""
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE tickets 
                SET status = 'terminated',
                    terminated_at = NOW(),
                    terminated_by = %s,
                    termination_reason = %s,
                    approval_status = 'terminated'
                WHERE ticket_id = %s
            """, (terminated_by, reason, ticket_id))
            
            affected = cursor.rowcount
            conn.commit()
            
            return affected > 0
    
    def get_ticket_details(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        """Get details of a specific ticket"""
        logger.info(f"get_ticket_details called with ticket_id: '{ticket_id}'")
        
        # Ensure ticket_id is normalized
        ticket_id = ticket_id.strip().lower()
        
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Get ticket info
            cursor.execute("""
                SELECT * FROM tickets 
                WHERE LOWER(ticket_id) = %s
            """, (ticket_id,))
            
            ticket = cursor.fetchone()
            
            if not ticket:
                logger.warning(f"No ticket found for ID: {ticket_id}")
                return None
            
            # Get initial details
            cursor.execute("""
                SELECT field_name, field_value 
                FROM ticket_details 
                WHERE ticket_id = %s AND is_initial = TRUE
            """, (ticket['ticket_id'],))
            
            initial_details = {row['field_name']: row['field_value'] for row in cursor.fetchall()}
            
            # Get updates
            cursor.execute("""
                SELECT update_timestamp, updated_fields 
                FROM ticket_updates 
                WHERE ticket_id = %s 
                ORDER BY update_timestamp
            """, (ticket['ticket_id'],))
            
            updates = []
            for row in cursor.fetchall():
                updates.append({
                    'timestamp': row['update_timestamp'].isoformat(),
                    'details': json.loads(row['updated_fields']) if row['updated_fields'] else {}
                })
            
            # Convert datetime objects to strings
            for key, value in ticket.items():
                if isinstance(value, datetime):
                    ticket[key] = value.isoformat()
            
            ticket['initial_details'] = initial_details
            ticket['updates'] = updates
            
            return ticket
    
    def get_complete_ticket_details(self, ticket_id: str) -> Dict[str, Any]:
        """Get complete ticket details including all updates"""
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Get all details (initial + updates) ordered by creation time
            cursor.execute("""
                SELECT field_name, field_value 
                FROM ticket_details 
                WHERE ticket_id = %s 
                ORDER BY created_at DESC
            """, (ticket_id,))
            
            # Use the most recent value for each field
            details = {}
            for row in cursor.fetchall():
                if row['field_name'] not in details:
                    details[row['field_name']] = row['field_value']
            
            return details
    
    def get_sender_tickets(self, sender: str) -> List[Dict[str, Any]]:
        """Get all active tickets from a sender"""
        with self.db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            cursor.execute("""
                SELECT t.ticket_id, t.created_at,
                       td.field_value as job_title
                FROM tickets t
                LEFT JOIN ticket_details td ON t.ticket_id = td.ticket_id 
                    AND td.field_name = 'job_title' AND td.is_initial = TRUE
                WHERE t.sender = %s AND t.status != 'terminated'
            """, (sender,))
            
            tickets = []
            for row in cursor.fetchall():
                tickets.append({
                    'id': row['ticket_id'],
                    'job_title': row['job_title'] or 'Unknown',
                    'created': row['created_at'].isoformat()
                })
            
            return tickets

# ============================================================================
# EMAIL HANDLER WITH LLM APPROVAL EMAIL GENERATION
# ============================================================================

class EmailHandler:
    """Handles email operations"""
    
    def __init__(self, email_address: str, password: str, imap_server: str, 
                 smtp_server: str, smtp_port: int, db_manager: DatabaseManager, 
                 response_generator_agent=None):
        self.email_address = email_address
        self.password = password
        self.imap_server = imap_server
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.db_manager = db_manager
        self.ticket_manager = TicketManager(db_manager)
        self.approval_manager = ApprovalManager(db_manager)
        self.response_generator_agent = response_generator_agent
    
    def set_response_generator(self, agent):
        """Set the response generator agent after initialization"""
        self.response_generator_agent = agent
    
    def send_approval_email(self, hr_email: str, ticket_id: str, 
                          job_details: Dict[str, Any], approval_token: str) -> bool:
        """Send approval request email to HR using LLM to generate content"""
        try:
            # Use LLM to generate the approval email content
            if self.response_generator_agent:
                prompt = f"""Generate a professional approval request email for HR.

Context:
- A new job posting request has been received
- HR approval is required before it appears on the website
- Ticket ID: {ticket_id}
- Approval Token: {approval_token}

Job Details:
- Job Title: {job_details.get('job_title', 'NOT_FOUND')}
- Location: {job_details.get('location', 'NOT_FOUND')}
- Experience Required: {job_details.get('experience_required', 'NOT_FOUND')}
- Salary Range: {job_details.get('salary_range', 'NOT_FOUND')}
- Employment Type: {job_details.get('employment_type', 'NOT_FOUND')}
- Application Deadline: {job_details.get('deadline', 'NOT_FOUND')}
- Job Description: {job_details.get('job_description', 'NOT_FOUND')}
- Required Skills: {job_details.get('required_skills', 'NOT_FOUND')}

Instructions to include in the email:
- To approve: Reply with "APPROVE {approval_token}"
- To reject: Reply with "REJECT {approval_token} [reason]"
- Once approved, the job will automatically appear on the website

Make the email professional, clear, and include all job details in a well-formatted way.
Start with "Dear HR Team," and end with "Best regards,\nAI Email Assistant"
"""
                
                response = self.response_generator_agent.generate_reply(
                    messages=[{"content": prompt, "role": "user"}]
                )
                
                # Clean the response
                email_body = clean_response_text(response)
                
                # If LLM response is too short or empty, use fallback
                if not email_body or len(email_body.strip()) < 50:
                    logger.warning("LLM generated insufficient content, using fallback")
                    email_body = self._get_fallback_approval_email(hr_email, ticket_id, job_details, approval_token)
            else:
                # If no LLM agent available, use fallback
                logger.warning("No LLM agent available for approval email generation")
                email_body = self._get_fallback_approval_email(hr_email, ticket_id, job_details, approval_token)
            
            # Send the email
            msg = EmailMessage()
            msg['Subject'] = f"[APPROVAL REQUIRED] Job Posting - {job_details.get('job_title', 'Unknown Position')}"
            msg['From'] = self.email_address
            msg['To'] = hr_email
            msg.set_content(email_body)
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_address, self.password)
                server.send_message(msg)
            
            logger.info(f"Approval email sent to {hr_email} for ticket {ticket_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending approval email: {e}")
            return False
    
    def _get_fallback_approval_email(self, hr_email: str, ticket_id: str, 
                                    job_details: Dict[str, Any], approval_token: str) -> str:
        """Fallback approval email template if LLM fails"""
        job_summary = f"""
Job Title: {job_details.get('job_title', 'NOT_FOUND')}
Location: {job_details.get('location', 'NOT_FOUND')}
Experience Required: {job_details.get('experience_required', 'NOT_FOUND')}
Salary Range: {job_details.get('salary_range', 'NOT_FOUND')}
Employment Type: {job_details.get('employment_type', 'NOT_FOUND')}
Application Deadline: {job_details.get('deadline', 'NOT_FOUND')}

Job Description:
{job_details.get('job_description', 'NOT_FOUND')}

Required Skills:
{job_details.get('required_skills', 'NOT_FOUND')}
"""
        
        return f"""Dear HR Team,

A new job posting request has been received and requires your approval before it appears on the website.

TICKET ID: {ticket_id}
APPROVAL TOKEN: {approval_token}

JOB DETAILS:
{job_summary}

TO APPROVE AND PUBLISH THIS JOB:
Reply to this email with: APPROVE {approval_token}

TO REJECT THIS POSTING:
Reply to this email with: REJECT {approval_token} [reason]

Note: Once approved, the job will automatically appear on the website.

Best regards,
AI Email Assistant"""
    
    def process_approval_response(self, email_body: str, sender: str) -> Tuple[bool, str]:
        """Process approval/rejection responses from HR"""
        logger.info(f"Processing approval response from {sender}")
        logger.info(f"Email body preview: {email_body[:200]}...")
        
        # Look for approval pattern
        approve_match = re.search(r'APPROVE\s+([a-zA-Z0-9]{32})', email_body, re.IGNORECASE)
        if approve_match:
            token = approve_match.group(1)
            logger.info(f"Found APPROVE token: {token}")
            
            # Process the approval
            success, message, ticket_id = self.approval_manager.process_approval(token)
            
            if success and ticket_id:
                # Update the ticket status
                if self.ticket_manager.approve_ticket(ticket_id):
                    return True, f"Job approved and published to website. Ticket ID: {ticket_id}"
                else:
                    return True, "Approval processed but failed to update ticket status"
            else:
                logger.warning(f"Approval processing failed: {message}")
                return True, message
        
        # Look for rejection pattern
        reject_match = re.search(r'REJECT\s+([a-zA-Z0-9]{32})\s*(.*)', email_body, re.IGNORECASE)
        if reject_match:
            token = reject_match.group(1)
            reason = reject_match.group(2).strip() or "No reason provided"
            logger.info(f"Found REJECT token: {token} with reason: {reason}")
            
            success, message, ticket_id = self.approval_manager.process_rejection(token, reason)
            
            if success and ticket_id:
                # Update the ticket with rejection info
                with self.db_manager.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE tickets 
                        SET approval_status = 'rejected',
                            approved = FALSE,
                            rejected_at = NOW(),
                            rejection_reason = %s
                        WHERE ticket_id = %s
                    """, (reason, ticket_id))
                    conn.commit()
                    
                logger.info(f"Updated ticket {ticket_id} as rejected")
            
            return True, message
        
        logger.info("No valid approval/rejection command found")
        return False, "No valid approval/rejection command found"
    
    def fetch_emails(self, max_emails: int = 10, folder: str = "INBOX") -> Tuple[List[Tuple], Any]:
        """Connect to email server and fetch unread emails"""
        try:
            logger.info(f"Connecting to {self.imap_server}...")
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_address, self.password)
            mail.select(folder)
            
            status, email_ids = mail.search(None, '(UNSEEN)')
            email_id_list = email_ids[0].split()
            
            if not email_id_list:
                logger.info("No unread emails found")
                return [], mail
            
            emails = []
            for e_id in email_id_list[-max_emails:]:
                status, msg_data = mail.fetch(e_id, '(RFC822)')
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                emails.append((e_id, msg))
            
            logger.info(f"Found {len(emails)} emails to process")
            return emails, mail
            
        except Exception as e:
            logger.error(f"Error fetching emails: {e}")
            return [], None
    
    def extract_email_body(self, msg: email.message.Message) -> str:
        """Extract the body text from an email message"""
        body = ""
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                if "attachment" in content_disposition:
                    continue
                
                if content_type == "text/plain" and "attachment" not in content_disposition:
                    body_bytes = part.get_payload(decode=True)
                    if body_bytes:
                        try:
                            body += body_bytes.decode()
                        except UnicodeDecodeError:
                            body += body_bytes.decode('latin-1')
                            
                elif content_type == "text/html" and "attachment" not in content_disposition and not body:
                    html_bytes = part.get_payload(decode=True)
                    if html_bytes:
                        try:
                            html_text = html_bytes.decode()
                        except UnicodeDecodeError:
                            html_text = html_bytes.decode('latin-1')
                        # Basic HTML stripping
                        body += re.sub('<[^<]+?>', '', html_text)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                try:
                    body = payload.decode()
                except UnicodeDecodeError:
                    body = payload.decode('latin-1')
                
                if msg.get_content_type() == "text/html":
                    body = re.sub('<[^<]+?>', '', body)
        
        return body.strip()
    
    def send_email(self, to_address: str, subject: str, body: str, 
                   reply_to_msg_id: Optional[str] = None) -> bool:
        """Send an email"""
        try:
            logger.info(f"Preparing to send email to: {to_address}")
            logger.info(f"Subject: {subject}")
            logger.info(f"Body preview: {body[:100]}...")
            
            msg = EmailMessage()
            msg['Subject'] = subject
            msg['From'] = self.email_address
            msg['To'] = to_address
            msg.set_content(body)
            
            if reply_to_msg_id:
                msg['In-Reply-To'] = reply_to_msg_id
                logger.info(f"Setting In-Reply-To: {reply_to_msg_id}")
            
            logger.info(f"Connecting to SMTP server {self.smtp_server}:{self.smtp_port}")
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_address, self.password)
                logger.info("SMTP login successful, sending message...")
                server.send_message(msg)
                logger.info(f"Email sent successfully to {to_address}")
            
            return True
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def mark_as_read(self, mail: imaplib.IMAP4_SSL, email_id: bytes) -> None:
        """Mark an email as read"""
        try:
            mail.store(email_id, '+FLAGS', '\\Seen')
            logger.info(f"Marked email {email_id.decode()} as read")
        except Exception as e:
            logger.error(f"Error marking email as read: {e}")
    
    def get_email_sender(self, msg: email.message.Message) -> str:
        """Extract sender email address"""
        sender = msg['From']
        if '<' in sender and '>' in sender:
            return sender[sender.find('<')+1:sender.find('>')]
        return sender
    
    def get_email_subject(self, msg: email.message.Message) -> str:
        """Extract subject from email message"""
        subject = msg['Subject'] or ""
        # For approval responses, keep the original subject
        if 'APPROVAL REQUIRED' in subject or 'APPROVE' in msg.get_payload(decode=True).decode()[:200] if msg.get_payload(decode=True) else '':
            return subject  # Keep original subject for approval threads
        elif not subject.lower().startswith('re:'):
            return f"Re: {subject}"
        return subject

# ============================================================================
# CUSTOM AUTOGEN AGENTS FOR GROQ
# ============================================================================

class EmailClassifierAgent(AssistantAgent):
    """Agent responsible for classifying emails"""
    
    def __init__(self, name: str, llm_config: Dict):
        system_message = """Classify emails. Return JSON only:
        {
            "is_hiring_email": true/false,
            "is_termination_request": true/false,
            "is_approval_response": true/false,
            "ticket_id": "id" or null,
            "confidence": 0.0-1.0,
            "reason": "brief reason"
        }
        
        Hiring keywords: job, hiring, position, salary, experience, update, revision
        Termination keywords: terminate, close ticket, position filled, cancel
        Approval keywords: APPROVE, REJECT (followed by 32-character token)
        
        IMPORTANT: Extract ticket ID if present. Look for patterns like:
        - Ticket ID: abc123def4 (EXACTLY 10 characters, only letters a-f and numbers 0-9)
        - Ticket #abc123def4
        - #abc123def4
        
        The ticket ID is ALWAYS exactly 10 characters long and contains only lowercase letters a-f and numbers 0-9.
        
        CRITICAL: Count the characters carefully! For example:
        - "ddaae6bf4d" is 10 characters ✓
        - "ddae6bf4d" is only 9 characters ✗
        
        Also check for approval tokens (32 characters, alphanumeric) after APPROVE or REJECT commands.
        
        If you find a ticket ID, extract it EXACTLY as written, preserving all characters."""
        super().__init__(
            name=name,
            system_message=system_message,
            llm_config=llm_config,
            human_input_mode="NEVER"
        )

class HiringDetailsExtractorAgent(AssistantAgent):
    """Agent responsible for extracting hiring details from emails"""
    
    def __init__(self, name: str, llm_config: Dict):
        system_message = """Extract hiring details from emails. Return ONLY a JSON object.
        
        Fields to extract:
        - job_title: The job position/role name (e.g., "Data Scientist", "Software Engineer")
        - location: Work location/city
        - experience_required: Years of experience (e.g., "5-10 years")
        - salary_range: Salary/compensation (e.g., "INR 18-28 Lakhs per annum")
        - job_description: Role description
        - required_skills: Skills needed (e.g., "GraphQL, Redis, MongoDB")
        - employment_type: Full-time/Part-time/Contract
        - deadline: Application deadline (e.g., "February 28, 2025")
        
        Use "NOT_FOUND" for missing fields.
        
        IMPORTANT: For updates, extract ONLY the fields that are mentioned as updated/revised."""
        super().__init__(
            name=name,
            system_message=system_message,
            llm_config=llm_config,
            human_input_mode="NEVER"
        )

class ResponseGeneratorAgent(AssistantAgent):
    """Agent responsible for generating email responses"""
    
    def __init__(self, name: str, llm_config: Dict):
        system_message = """Generate professional email responses. Return the complete email body text.
        
        For missing_details: Politely ask for the missing information
        For ticket_created: Confirm new ticket with all details and mention approval is needed
        For ticket_updated: Show what was updated
        For ticket_terminated: Confirm ticket closure
        For approval_sent: Confirm that approval request was sent
        For approval_request: Create a professional approval request email with all job details
        
        For approval request emails, format the job details clearly and include:
        - Clear instructions to APPROVE or REJECT with the token
        - All job details in a well-formatted structure
        - Professional tone suitable for HR
        
        Always start with "Dear [Name]," or "Dear HR Team," and end with "Best regards,\nAI Email Assistant"
        
        IMPORTANT: Return ONLY the email body text, no JSON or other formatting.
        
        Example format:
        Dear [Name],
        
        [Main content based on scenario]
        
        Best regards,
        AI Email Assistant"""
        super().__init__(
            name=name,
            system_message=system_message,
            llm_config=llm_config,
            human_input_mode="NEVER"
        )

class EmailProcessingOrchestrator(UserProxyAgent):
    """Orchestrator agent that manages the email processing workflow"""
    
    def __init__(self, name: str, email_handler: EmailHandler):
        super().__init__(
            name=name,
            human_input_mode="NEVER",
            max_consecutive_auto_reply=10,
            code_execution_config=False,
            llm_config=False,
            system_message="You are the orchestrator. Process emails step by step."
        )
        self.email_handler = email_handler
        self.ticket_manager = email_handler.ticket_manager
        self.approval_manager = email_handler.approval_manager
    
    def process_email_workflow(self, email_data: Dict[str, str], agents: Dict[str, AssistantAgent]) -> Dict[str, Any]:
        """Process email through the workflow"""
        results = {
            "sender": email_data["sender"],
            "subject": email_data["subject"],
            "action_taken": None,
            "response_sent": False
        }
        
        # First check if this is an approval response
        is_approval_response, approval_message = self.email_handler.process_approval_response(
            email_data["body"], 
            email_data["sender"]
        )
        
        logger.info(f"Approval response check: is_approval={is_approval_response}, message={approval_message}")
        
        if is_approval_response:
            logger.info("Processing approval response workflow")
            results["action_taken"] = approval_message
            
            # Always try to extract token and send appropriate response
            token_match = re.search(r'APPROVE\s+([a-zA-Z0-9]{32})', email_data["body"], re.IGNORECASE)
            if not token_match:
                token_match = re.search(r'REJECT\s+([a-zA-Z0-9]{32})', email_data["body"], re.IGNORECASE)
            
            response_body = ""
            
            if token_match:
                token = token_match.group(1)
                logger.info(f"Found token: {token}")
                
                # Get approval info from database
                with self.email_handler.db_manager.get_connection() as conn:
                    cursor = conn.cursor(dictionary=True)
                    cursor.execute("""
                        SELECT pa.*, t.sender as original_sender,
                               td.field_value as job_title
                        FROM pending_approvals pa
                        JOIN tickets t ON pa.ticket_id = t.ticket_id
                        LEFT JOIN ticket_details td ON t.ticket_id = td.ticket_id 
                            AND td.field_name = 'job_title' AND td.is_initial = TRUE
                        WHERE pa.approval_token = %s
                    """, (token,))
                    
                    approval_info = cursor.fetchone()
                
                if approval_info:
                    ticket_id = approval_info['ticket_id']
                    approval_status = approval_info['status']
                    job_title = approval_info['job_title'] or 'Unknown Position'
                    
                    # Generate appropriate response based on the approval status
                    if "already processed" in approval_message:
                        # Already processed (approved or rejected)
                        if approval_status == 'approved':
                            response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your approval response.

The job posting for "{job_title}" (Ticket ID: {ticket_id}) was already approved and is currently published on the website.

This approval was processed previously, so no further action is needed.

Status Summary:
- Ticket ID: {ticket_id}
- Job Title: {job_title}
- Status: Already Approved and Published
- Visibility: Live on Website

If you need to make any changes or terminate this posting, please reply with:
"Please terminate ticket {ticket_id}"

Best regards,
AI Email Assistant"""
                        else:
                            # Already rejected
                            response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your response.

This job posting was already processed and rejected previously.

Status Summary:
- Ticket ID: {ticket_id}
- Job Title: {job_title}
- Status: Already Rejected

If you would like to create a new job posting, please submit a new hiring request.

Best regards,
AI Email Assistant"""
                    
                    elif "approved and published" in approval_message:
                        # Just approved successfully
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your approval!

The job posting for "{job_title}" (Ticket ID: {ticket_id}) has been successfully approved and is now published on the website.

The job posting is now visible to candidates and ready to receive applications.

Status Summary:
- Ticket ID: {ticket_id}
- Job Title: {job_title}
- Status: Approved and Published
- Visibility: Live on Website

If you need to make any changes or terminate this posting in the future, please reply with:
"Please terminate ticket {ticket_id}"

Best regards,
AI Email Assistant"""
                    
                    elif "rejected" in approval_message:
                        # Just rejected
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your response.

The job posting for "{job_title}" (Ticket ID: {ticket_id}) has been rejected as requested.

The job posting will not appear on the website.

If you would like to create a new job posting with modifications, please submit a new hiring request with the updated details.

Best regards,
AI Email Assistant"""
                    
                    else:
                        # Other cases (e.g., invalid token)
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your response.

{approval_message}

Please verify the approval token and try again if needed.

Best regards,
AI Email Assistant"""
                else:
                    # Token not found in approvals
                    response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your approval response.

However, the approval token provided is not valid or has expired.

{approval_message}

Please verify the token and try again, or contact the system administrator.

Best regards,
AI Email Assistant"""
            else:
                # No token found in email
                response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your response.

{approval_message}

To approve or reject a job posting, please include the complete approval token in your response.

Best regards,
AI Email Assistant"""
            
            # Always send the response email for approval responses
            logger.info(f"About to send approval response email to {email_data['sender']}")
            logger.info(f"Response body length: {len(response_body)}")
            
            sent = self.email_handler.send_email(
                email_data["sender"],
                email_data["subject"],
                response_body,
                email_data.get("message_id")
            )
            
            if sent:
                logger.info("Approval response email sent successfully")
            else:
                logger.error("Failed to send approval response email")
            
            results["response_sent"] = sent
            
            # CRITICAL: Always return here to prevent further processing
            logger.info("Returning from approval response handler")
            return results
        
        # Continue with existing workflow for non-approval emails
        
        # Step 1: Classify email
        classification_prompt = f"""
        Classify this email:
        Subject: {email_data['subject']}
        Body: {email_data['body']}
        """
        
        classification_response = agents["classifier"].generate_reply(
            messages=[{"content": classification_prompt, "role": "user"}]
        )
        
        # Debug logging
        logger.info(f"Classification response type: {type(classification_response)}")
        logger.info(f"Classification response: {classification_response}")
        
        # Parse classification response
        classification = extract_json_from_text(classification_response)
        
        if classification is None:
            # Fallback classification
            logger.warning("Failed to parse classification response, using fallback")
            classification = {
                "is_hiring_email": self._is_hiring_email(email_data['subject'], email_data['body']),
                "is_termination_request": self._is_termination_request(email_data['body']),
                "ticket_id": self._extract_ticket_id(email_data['body']),
                "confidence": 0.7,
                "reason": "Fallback classification"
            }
        
        # Always check for ticket ID in body if not found by classifier or if it's invalid
        classifier_ticket_id = classification.get("ticket_id")
        if classifier_ticket_id and len(str(classifier_ticket_id)) != 10:
            logger.warning(f"Classifier extracted invalid ticket ID (wrong length): '{classifier_ticket_id}' (length: {len(str(classifier_ticket_id))})")
            classifier_ticket_id = None
            
        if not classifier_ticket_id:
            extracted_ticket_id = self._extract_ticket_id(email_data['body'])
            if extracted_ticket_id:
                logger.info(f"Classifier missed/invalid ticket ID, but found it manually: {extracted_ticket_id}")
                classification["ticket_id"] = extracted_ticket_id
        else:
            # Double-check the classifier's extraction with our own extraction
            manual_ticket_id = self._extract_ticket_id(email_data['body'])
            if manual_ticket_id and manual_ticket_id != classifier_ticket_id:
                logger.warning(f"Classifier extracted '{classifier_ticket_id}' but manual extraction found '{manual_ticket_id}'")
                logger.info(f"Using manually extracted ticket ID: {manual_ticket_id}")
                classification["ticket_id"] = manual_ticket_id
        
        logger.info(f"Final classification result: {classification}")
        
        # Step 2: Process based on classification
        if classification.get("is_termination_request"):
            ticket_id = classification.get("ticket_id")
            if ticket_id and self.ticket_manager.get_ticket_details(ticket_id):
                # Check if the sender has permission to terminate
                ticket_details = self.ticket_manager.get_ticket_details(ticket_id)
                original_sender = ticket_details.get("sender", "")
                
                # Allow termination if sender is the original poster or if it's an approved ticket
                if email_data["sender"].lower() != original_sender.lower():
                    logger.warning(f"Sender {email_data['sender']} trying to terminate ticket created by {original_sender}")
                    results["action_taken"] = "Unauthorized termination attempt"
                    
                    response_body = f"""Dear {email_data['sender'].split('@')[0]},

You cannot terminate ticket {ticket_id} because it was created by {original_sender}.

Only the original job poster can terminate their tickets.

Best regards,
AI Email Assistant"""
                    
                    sent = self.email_handler.send_email(
                        email_data["sender"],
                        email_data["subject"],
                        response_body,
                        email_data.get("message_id")
                    )
                    results["response_sent"] = sent
                    return results
                
                # Terminate ticket
                reason = self._extract_termination_reason(email_data["body"])
                success = self.ticket_manager.terminate_ticket(ticket_id, email_data["sender"], reason)
                
                if success:
                    results["action_taken"] = f"Terminated ticket {ticket_id}"
                    
                    # Check if it was posted
                    was_posted = ticket_details.get("approval_status") == "approved"
                    
                    # Generate termination response
                    response_prompt = f"""
                    Generate a ticket termination confirmation email for:
                    Ticket ID: {ticket_id}
                    Sender: {email_data['sender']}
                    Reason: {reason}
                    Was Approved: {was_posted}
                    Scenario: ticket_terminated
                    """
                    
                    # Generate response
                    response_body = agents["response_generator"].generate_reply(
                        messages=[{"content": response_prompt, "role": "user"}]
                    )
                    
                    logger.info(f"Raw termination response body: {response_body}")
                    
                    # Clean the response body
                    response_body = clean_response_text(response_body)
                    
                    # If response is empty, create a fallback response
                    if not response_body or len(response_body.strip()) < 10:
                        logger.warning("Empty or too short termination response, using fallback")
                        posted_msg = ""
                        if was_posted:
                            posted_msg = "\n\nThe job posting has been removed from the website display."
                        
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your email. I have successfully terminated ticket {ticket_id}.

The hiring position has been closed as requested.
Reason: {reason}

The ticket status has been updated to 'terminated' in our system.{posted_msg}

Best regards,
AI Email Assistant"""
                    
                    logger.info(f"Final termination response body: {response_body}")
                    
                    # Send email
                    sent = self.email_handler.send_email(
                        email_data["sender"],
                        email_data["subject"],
                        response_body,
                        email_data.get("message_id")
                    )
                    results["response_sent"] = sent
                else:
                    results["action_taken"] = f"Failed to terminate ticket {ticket_id}"
            else:
                results["action_taken"] = "Termination request but no valid ticket ID found"
        
        elif classification.get("is_hiring_email"):
            # Check if this is an update to an existing ticket
            ticket_id = classification.get("ticket_id")
            
            # Check if subject indicates an update
            update_indicators = ['update', 'modify', 'change', 'revision', 'edit', 'revised', 'modification']
            subject_lower = email_data['subject'].lower()
            body_lower = email_data['body'].lower()
            is_update_request = any(indicator in subject_lower or indicator in body_lower for indicator in update_indicators)
            
            # If it's an update request but no ticket ID provided
            if is_update_request and not ticket_id:
                logger.info("Update request detected but no ticket ID provided")
                results["action_taken"] = "Update request missing ticket ID"
                
                # Find all active tickets from this sender
                sender_tickets = self.ticket_manager.get_sender_tickets(email_data['sender'])
                
                # Generate response asking for ticket ID
                if sender_tickets:
                    ticket_list = "\n".join([f"• Ticket ID: {t['id']} - {t['job_title']} (Created: {t['created'][:10]})" 
                                           for t in sender_tickets])
                    response_body = f"""Dear {email_data['sender'].split('@')[0]},

I noticed you're trying to update a hiring position, but the ticket ID is missing from your email.

Please include the ticket ID in your email to proceed with the update. Here are your active tickets:

{ticket_list}

Please resend your update email with the format:
Ticket ID: [10-character-id]

For example:
Ticket ID: abc123def4

Best regards,
AI Email Assistant"""
                else:
                    response_body = f"""Dear {email_data['sender'].split('@')[0]},

I noticed you're trying to update a hiring position, but I couldn't find the ticket ID in your email.

Additionally, I don't have any active hiring tickets associated with your email address.

If you have a ticket ID from a previous submission, please include it in your email with the format:
Ticket ID: [10-character-id]

If you're submitting a new hiring request, please remove update-related keywords from your email.

Best regards,
AI Email Assistant"""
                
                # Send email
                sent = self.email_handler.send_email(
                    email_data["sender"],
                    email_data["subject"],
                    response_body,
                    email_data.get("message_id")
                )
                results["response_sent"] = sent
                return results
            
            # Check if we have a valid ticket ID that exists
            if ticket_id:
                # Add extensive debugging
                logger.info(f"Checking for ticket ID: '{ticket_id}'")
                logger.info(f"Type of ticket_id: {type(ticket_id)}")
                logger.info(f"Length of ticket_id: {len(ticket_id)}")
                
                # Ensure ticket_id is lowercase and stripped
                ticket_id = ticket_id.strip().lower()
                logger.info(f"Normalized ticket_id: '{ticket_id}'")
                
                ticket_details = self.ticket_manager.get_ticket_details(ticket_id)
                logger.info(f"Ticket lookup result: {ticket_details is not None}")
                
                if ticket_details:
                    # Check if ticket is approved - don't allow updates
                    if ticket_details.get("approval_status") == "approved":
                        logger.warning(f"Cannot update ticket {ticket_id} - already approved")
                        results["action_taken"] = f"Cannot update approved ticket {ticket_id}"
                        
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your update request. However, I cannot update ticket {ticket_id} because it has already been approved and published to the website.

Once a job posting is approved and live on the website, it cannot be modified through email updates to maintain consistency for applicants.

If you need to make changes to this posting, please:
1. Send a termination request for the current ticket
2. Create a new job posting with the updated information

To terminate this ticket, reply with:
"Please terminate ticket {ticket_id}"

Best regards,
AI Email Assistant"""
                        
                        # Send email
                        sent = self.email_handler.send_email(
                            email_data["sender"],
                            email_data["subject"],
                            response_body,
                            email_data.get("message_id")
                        )
                        results["response_sent"] = sent
                        return results
                    
                    # We have a valid ticket ID that exists - proceed with update
                    logger.info(f"Processing update for existing ticket: {ticket_id}")
                    logger.info(f"Ticket details: {ticket_details}")
                    
                    # Extract only the updated details
                    extraction_prompt = f"""
                    Extract ONLY the updated hiring details from this email update.
                    This is an UPDATE to an existing position, not a new position.
                    
                    Email content:
                    {email_data['body']}
                    
                    Look for these specific updates mentioned in the email:
                    - If "Salary Range:" is mentioned, extract the salary value
                    - If "Experience Required:" is mentioned, extract the years
                    - If "Additional Skills:" is mentioned, extract the skills
                    - If "Application Deadline:" is mentioned, extract the date
                    - Any other fields that are being updated
                    
                    Return ONLY a JSON with the fields that are being updated.
                    """
                    
                    extraction_response = agents["extractor"].generate_reply(
                        messages=[{"content": extraction_prompt, "role": "user"}]
                    )
                    
                    logger.info(f"Extraction response: {extraction_response}")
                    
                    # Parse extraction response
                    extracted_details = extract_json_from_text(extraction_response)
                    
                    if extracted_details is None:
                        logger.warning("No JSON found in extraction response, using update-specific extraction")
                        extracted_details = self._extract_update_details(email_data['body'], email_data['subject'])
                    
                    # For updates, we don't need all fields - only merge non-NOT_FOUND values
                    update_details = {}
                    for key, value in extracted_details.items():
                        if value and value != "NOT_FOUND" and len(str(value).strip()) > 0:
                            update_details[key] = str(value).strip()
                    
                    # Update the ticket with only the changed fields
                    existing_ticket = self.ticket_manager.get_ticket_details(ticket_id)
                    merged_details = existing_ticket.get("initial_details", {}).copy()
                    
                    # Apply all previous updates
                    for update in existing_ticket.get("updates", []):
                        merged_details.update(update.get("details", {}))
                    
                    # Apply new updates
                    merged_details.update(update_details)
                    
                    # Create or update ticket (this will append to updates array)
                    # For updates, use the existing ticket_id instead of generating a new one
                    ticket_id_returned, is_update, status = self.ticket_manager.create_or_update_ticket_with_id(
                        ticket_id,  # Use the existing ticket ID
                        email_data["sender"],
                        email_data["subject"],
                        update_details,  # Only save the updates
                        email_data["timestamp"]
                    )
                    
                    if status == "approved_locked":
                        results["action_taken"] = f"Cannot update approved ticket {ticket_id}"
                        # Response already sent above
                        return results
                    
                    results["action_taken"] = f"Ticket {ticket_id} updated"
                    
                    # Generate update confirmation response
                    response_prompt = f"""
                    Generate a ticket update confirmation email:
                    Ticket ID: {ticket_id}
                    Sender: {email_data['sender']}
                    Updated fields: {json.dumps(update_details)}
                    All current details: {json.dumps(merged_details)}
                    Scenario: ticket_updated
                    """
                    
                    # Generate and send response
                    response_body = agents["response_generator"].generate_reply(
                        messages=[{"content": response_prompt, "role": "user"}]
                    )
                    
                    logger.info(f"Raw response body: {response_body}")
                    
                    # Clean the response body
                    response_body = clean_response_text(response_body)
                    
                    # If response is empty, create a fallback response
                    if not response_body or len(response_body.strip()) < 10:
                        logger.warning("Empty or too short response, using fallback")
                        # Format the updates nicely
                        update_lines = []
                        field_names = {
                            "job_title": "Job Title",
                            "location": "Location",
                            "experience_required": "Experience Required",
                            "salary_range": "Salary Range",
                            "job_description": "Job Description",
                            "required_skills": "Required Skills",
                            "employment_type": "Employment Type",
                            "deadline": "Application Deadline"
                        }
                        
                        for key, value in update_details.items():
                            if value and value != "NOT_FOUND":
                                update_lines.append(f"• {field_names.get(key, key)}: {value}")
                        
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for the update on ticket {ticket_id}.

The following information has been updated:
{chr(10).join(update_lines)}

The ticket has been successfully updated in our system.

Best regards,
AI Email Assistant"""
                    
                    logger.info(f"Final response body: {response_body}")
                    
                    # Send email
                    sent = self.email_handler.send_email(
                        email_data["sender"],
                        email_data["subject"],
                        response_body,
                        email_data.get("message_id")
                    )
                    results["response_sent"] = sent
                    
                else:
                    # We have a ticket ID but it doesn't exist
                    logger.warning(f"Ticket ID {ticket_id} provided but not found in system")
                    results["action_taken"] = f"Ticket ID {ticket_id} not found"
                    
                    # List all active tickets from this sender
                    sender_tickets = self.ticket_manager.get_sender_tickets(email_data['sender'])
                    
                    # Generate error response with available tickets
                    if sender_tickets:
                        ticket_list = "\n".join([f"• Ticket ID: {t['id']} - {t['job_title']} (Created: {t['created'][:10]})" 
                                               for t in sender_tickets])
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your email. However, I couldn't find ticket ID {ticket_id} in our system.

Here are your active tickets:
{ticket_list}

Please verify the ticket ID and try again. The ticket ID should be exactly 10 characters long and match one of the tickets listed above.

Best regards,
AI Email Assistant"""
                    else:
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your email. However, I couldn't find ticket ID {ticket_id} in our system.

Additionally, I don't have any active hiring tickets associated with your email address.

Please verify the ticket ID or submit a new hiring request.

Best regards,
AI Email Assistant"""
                    
                    # Send email
                    sent = self.email_handler.send_email(
                        email_data["sender"],
                        email_data["subject"],
                        response_body,
                        email_data.get("message_id")
                    )
                    results["response_sent"] = sent
                    return results
            
            else:
                # This is a new hiring email (no ticket ID and not an update request)
                # Extract hiring details
                extraction_prompt = f"""
                Extract hiring details from this email:
                Subject: {email_data['subject']}
                Body: {email_data['body']}
                """
                
                extraction_response = agents["extractor"].generate_reply(
                    messages=[{"content": extraction_prompt, "role": "user"}]
                )
                
                logger.info(f"Extraction response: {extraction_response}")
                
                # Parse extraction response
                extracted_details = extract_json_from_text(extraction_response)
                
                if extracted_details is None:
                    logger.warning("No JSON found in extraction response, using fallback")
                    extracted_details = self._fallback_extraction(email_data['body'])
                
                # Clean extracted details
                cleaned_details = {}
                for key in REQUIRED_HIRING_DETAILS:
                    value = extracted_details.get(key, "NOT_FOUND")
                    if value and value != "NOT_FOUND" and len(str(value).strip()) > 0:
                        cleaned_details[key] = str(value).strip()
                    else:
                        cleaned_details[key] = "NOT_FOUND"
                
                extracted_details = cleaned_details
                logger.info(f"Extracted details: {extracted_details}")
                
                # Check for missing details
                missing_details = [
                    key for key in REQUIRED_HIRING_DETAILS 
                    if extracted_details.get(key) == "NOT_FOUND"
                ]
                
                if missing_details:
                    results["action_taken"] = "Missing required details"
                    # Generate missing details response
                    response_prompt = f"""
                    Generate an email asking for missing hiring details:
                    Sender: {email_data['sender']}
                    Missing fields: {', '.join(missing_details)}
                    Scenario: missing_details
                    """
                else:
                    # Create or update ticket
                    ticket_id, is_update, status = self.ticket_manager.create_or_update_ticket(
                        email_data["sender"],
                        email_data["subject"],
                        extracted_details,
                        email_data["timestamp"]
                    )
                    
                    if status == "terminated":
                        results["action_taken"] = f"Ticket {ticket_id} is already terminated"
                    elif status == "approved_locked":
                        results["action_taken"] = f"Cannot update approved ticket {ticket_id}"
                        
                        response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your submission. However, ticket {ticket_id} has already been approved and published to the website.

Approved job postings cannot be modified to maintain consistency for applicants.

If you need to make changes, please:
1. Send a termination request for ticket {ticket_id}
2. Submit a new job posting with the updated information

To terminate, reply with: "Please terminate ticket {ticket_id}"

Best regards,
AI Email Assistant"""
                        
                        sent = self.email_handler.send_email(
                            email_data["sender"],
                            email_data["subject"],
                            response_body,
                            email_data.get("message_id")
                        )
                        results["response_sent"] = sent
                        return results
                    else:
                        action = "updated" if is_update else "created"
                        results["action_taken"] = f"Ticket {ticket_id} {action}"
                        
                        # For new complete tickets, create approval request
                        if action == "created":
                            job_details = self.ticket_manager.get_complete_ticket_details(ticket_id)
                            
                            # Create approval request
                            approval_token = self.approval_manager.create_approval_request(
                                ticket_id, 
                                job_details, 
                                email_data["sender"]
                            )
                            
                            # Update ticket with approval token
                            with self.email_handler.db_manager.get_connection() as conn:
                                cursor = conn.cursor()
                                cursor.execute("""
                                    UPDATE tickets 
                                    SET approval_token = %s
                                    WHERE ticket_id = %s
                                """, (approval_token, ticket_id))
                                conn.commit()
                            
                            # Send approval email
                            approval_sent = self.email_handler.send_approval_email(
                                email_data["sender"],  # Send to the HR who submitted
                                ticket_id,
                                job_details,
                                approval_token
                            )
                            
                            if approval_sent:
                                results["action_taken"] += " - Approval request sent"
                                response_prompt = f"""
                                Generate a ticket created confirmation email that mentions approval is needed:
                                Ticket ID: {ticket_id}
                                Sender: {email_data['sender']}
                                Details: {json.dumps(extracted_details)}
                                Scenario: ticket_created with approval_sent
                                """
                        else:
                            # Generate update confirmation response
                            response_prompt = f"""
                            Generate a ticket {action} confirmation email:
                            Ticket ID: {ticket_id}
                            Sender: {email_data['sender']}
                            Details: {json.dumps(extracted_details)}
                            Scenario: ticket_{action}
                            """
                
                # Generate and send response
                if 'response_prompt' in locals():
                    response_body = agents["response_generator"].generate_reply(
                        messages=[{"content": response_prompt, "role": "user"}]
                    )
                    
                    logger.info(f"Raw response body: {response_body}")
                    
                    # Clean the response body
                    response_body = clean_response_text(response_body)
                    
                    # If response is empty, create a fallback response based on action
                    if not response_body or len(response_body.strip()) < 10:
                        logger.warning("Empty or too short response, using fallback")
                        if "Missing required details" in results["action_taken"]:
                            response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for your hiring request. However, I noticed some important details are missing:

{chr(10).join(['• ' + field.replace('_', ' ').title() for field in missing_details])}

Please provide these details so I can properly process your hiring request.

Best regards,
AI Email Assistant"""
                        elif "created" in results["action_taken"] and "Approval request sent" in results["action_taken"]:
                            response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for submitting the hiring request. I have created a new ticket for this position.

Ticket ID: {ticket_id}
Status: Pending Approval

Details recorded:
{json.dumps(extracted_details, indent=2)}

IMPORTANT: An approval request has been sent to you. Please check your email and follow the instructions to approve this job posting before it appears on the website.

Best regards,
AI Email Assistant"""
                        elif "created" in results["action_taken"]:
                            response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for submitting the hiring request. I have created a new ticket for this position.

Ticket ID: {ticket_id}
Status: New

Details recorded:
{json.dumps(extracted_details, indent=2)}

This information has been saved in our system.

Best regards,
AI Email Assistant"""
                        elif "updated" in results["action_taken"]:
                            response_body = f"""Dear {email_data['sender'].split('@')[0]},

Thank you for the update. The ticket has been successfully updated.

Ticket ID: {ticket_id}
Status: Updated

Your changes have been recorded in our system.

Best regards,
AI Email Assistant"""
                    
                    logger.info(f"Final response body: {response_body}")
                    
                    # Send email
                    sent = self.email_handler.send_email(
                        email_data["sender"],
                        email_data["subject"],
                        response_body,
                        email_data.get("message_id")
                    )
                    results["response_sent"] = sent
        
        else:
            results["action_taken"] = "Not a hiring-related email"
        
        return results
    
    def _extract_update_details(self, body: str, subject: str) -> Dict[str, str]:
        """Extract only updated fields from an update email"""
        updates = {}
        
        # Extract job title from subject if mentioned
        title_match = re.search(r'(?:Update on|Updated?)\s+(.+?)\s+Position', subject, re.IGNORECASE)
        if title_match:
            updates["job_title"] = title_match.group(1).strip()
        
        # Extract specific updated fields
        update_patterns = {
            "salary_range": [
                r"Salary Range:\s*([^\n]+?)(?:\s*\(revised\))?(?:\n|$)",
                r"Salary:\s*([^\n]+?)(?:\s*\(revised\))?(?:\n|$)"
            ],
            "experience_required": [
                r"Experience Required:\s*([^\n]+?)(?:\s*\(updated\))?(?:\n|$)",
                r"Experience:\s*([^\n]+?)(?:\s*\(updated\))?(?:\n|$)"
            ],
            "required_skills": [
                r"Additional Skills:\s*([^\n]+?)(?:\n|$)",
                r"Skills:\s*([^\n]+?)(?:\n|$)"
            ],
            "deadline": [
                r"Application Deadline:\s*(?:Extended to\s*)?([^\n]+?)(?:\n|$)",
                r"Deadline:\s*(?:Extended to\s*)?([^\n]+?)(?:\n|$)"
            ],
            "location": [
                r"Location:\s*([^\n]+?)(?:\n|$)",
                r"Office:\s*([^\n]+?)(?:\n|$)"
            ]
        }
        
        for field, patterns in update_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, body, re.IGNORECASE | re.MULTILINE)
                if match:
                    value = match.group(1).strip()
                    # Clean up the value
                    value = value.rstrip(',.')
                    updates[field] = value
                    break
        
        return updates
    
    def _extract_termination_reason(self, email_body: str) -> str:
        """Extract termination reason from email"""
        reason_patterns = [
            r'reason[:\s]+([^\n]+)',
            r'because[:\s]+([^\n]+)',
            r'position (?:has been )?filled',
            r'no longer (?:need|require|hiring)'
        ]
        
        for pattern in reason_patterns:
            match = re.search(pattern, email_body, re.IGNORECASE)
            if match:
                return match.group(0) if match.lastindex is None else match.group(1)
        
        return "No specific reason provided"
    
    def _is_hiring_email(self, subject: str, body: str) -> bool:
        """Check if email is hiring related"""
        hiring_keywords = [
            'job', 'hiring', 'recruitment', 'position', 'vacancy', 'opening', 
            'career', 'employment', 'interview', 'candidate', 'application',
            'job description', 'job opportunity', 'job posting', 
            'we are hiring', 'now hiring', 'seeking', 'looking for',
            'talent acquisition', 'talent search', 'workforce',
            'join our team', 'join team', 'open role', 'current openings',
            'resume', 'cv', 'hiring manager', 'job requirements',
            'job responsibilities', 'job qualifications',
            'hiring process', 'job application', 'job search',
            'professional opportunity', 'career opportunity','salary range', 
            'salary', 'experience', 'deadline', 'update', 
            'revised', 'requirement', 'skills', 'location'
        ]
        
        combined_text = f"{subject} {body}".lower()
        matches = sum(1 for keyword in hiring_keywords if keyword in combined_text)
        return matches >= 2
    
    def _is_termination_request(self, body: str) -> bool:
        """Check if email is a termination request"""
        termination_keywords = [
            'terminate', 'termination', 'close ticket', 'cancel ticket', 
            'close this ticket', 'cancel this position', 'position filled',
            'job filled', 'hiring closed', 'position closed', 'no longer hiring',
            'withdraw', 'withdrawal', 'cancel job', 'terminate ticket'
        ]
        
        body_lower = body.lower()
        return any(keyword in body_lower for keyword in termination_keywords)
    
    def _extract_ticket_id(self, body: str) -> Optional[str]:
        """Extract ticket ID from email body"""
        # Debug logging
        logger.info(f"Attempting to extract ticket ID from body: {body[:200]}...")
        
        patterns = [
            r'ticket\s*id\s*[:\s]*([a-fA-F0-9]{10})',
            r'ticket\s*#\s*([a-fA-F0-9]{10})',
            r'ticket\s*number\s*[:\s]*([a-fA-F0-9]{10})',
            r'#([a-fA-F0-9]{10})\b',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                ticket_id = match.group(1).lower()  # Convert to lowercase since MD5 hash is lowercase
                logger.info(f"Successfully extracted ticket ID: {ticket_id}")
                return ticket_id
        
        logger.warning(f"No ticket ID found in email body. Checked patterns: {patterns}")
        return None
    
    def _fallback_extraction(self, body: str) -> Dict[str, str]:
        """Fallback extraction using regex patterns"""
        extracted = {}
        
        patterns = {
            "job_title": [
                r"(?:position|job title|role)[\s:]+([^\n:]+?)(?:\n|:|$)",
                r"(?:hiring for|seeking)\s+(?:a\s+)?([^\n]+?)(?:\s+position|\s+role|\n|:|$)",
                r"for the\s+([^\n]+?)\s+Position"
            ],
            "location": [
                r"(?:location|office|city)[\s:]+([^\n:]+)",
                r"(?:based in|office in)\s+([^\n:]+)"
            ],
            "experience_required": [
                r"Experience Required[\s:]+([^\n:]+)",
                r"Experience[\s:]+(\d+[-\-]\d+\s+years?)",
                r"(\d+[-\-]\d+\s+years?)(?:\s+\(updated\))?",
                r"(\d+\+?\s+years?)"
            ],
            "salary_range": [
                r"Salary Range[\s:]+([^\n:]+?)(?:\s+\(revised\))?",
                r"(?:salary|compensation|ctc)[\s:]+([^\n:]+)",
                r"(?:INR|Rs\.?)\s*([\d,\s\-]+(?:Lakhs?|LPA|L)[^\n]*)"
            ],
            "deadline": [
                r"(?:deadline|last date|apply by|closing date|application deadline)[\s:]+([^\n:]+)",
                r"Extended to\s+([^\n:]+)"
            ],
            "required_skills": [
                r"Additional Skills[\s:]+([^\n:]+)",
                r"(?:required skills|skills|requirements|qualifications|technical requirements)[\s:]+([^\n:]+)",
                r"(?:must have|should have)[\s:]+([^\n:]+)"
            ]
        }
        
        for field, field_patterns in patterns.items():
            for pattern in field_patterns:
                match = re.search(pattern, body, re.IGNORECASE | re.MULTILINE)
                if match:
                    value = match.group(1).strip()
                    # Clean up the value
                    value = value.rstrip(',.')
                    if field == "salary_range":
                        # Remove redundant "Range:" prefix if present
                        value = re.sub(r'^Range:\s*', '', value, flags=re.IGNORECASE)
                    extracted[field] = value
                    break
            if field not in extracted:
                extracted[field] = "NOT_FOUND"
        
        # Fill remaining fields
        for field in REQUIRED_HIRING_DETAILS:
            if field not in extracted:
                extracted[field] = "NOT_FOUND"
        
        return extracted

#!/usr/bin/env python3
"""
Debug version with more status messages
Add this to the end of your existing code
"""

# ============================================================================
# MAIN EMAIL PROCESSING SYSTEM WITH MYSQL
# ============================================================================

class EmailHiringBotSystem:
    """Main system orchestrating all agents"""
    
    def __init__(self, email_handler: EmailHandler, llm_config: Dict):
        self.email_handler = email_handler
        self.llm_config = llm_config
        
        # Initialize agents
        self.agents = {
            "classifier": EmailClassifierAgent("EmailClassifier", llm_config),
            "extractor": HiringDetailsExtractorAgent("DetailsExtractor", llm_config),
            "response_generator": ResponseGeneratorAgent("ResponseGenerator", llm_config)
        }
        
        # Set the response generator for email handler
        self.email_handler.set_response_generator(self.agents["response_generator"])
        
        self.orchestrator = EmailProcessingOrchestrator("Orchestrator", email_handler)
    
    def process_emails(self) -> str:
        """Process all unread emails"""
        print("\n" + "="*60)
        print("STARTING EMAIL PROCESSING")
        print("="*60)
        
        logger.info("Starting Email Hiring Bot with AutoGen, Groq, and MySQL")
        
        print("Fetching unread emails...")
        emails, mail = self.email_handler.fetch_emails(max_emails=MAX_EMAILS_TO_PROCESS)
        
        if not mail:
            print("❌ Could not connect to email server.")
            return "Could not connect to email server."
        
        if not emails:
            print("📭 No unread emails found.")
            print("\nTo test the bot:")
            print("1. Send an email to:", EMAIL_ADDRESS)
            print("2. Use subject like: 'Hiring: Software Developer Position'")
            print("3. Include job details in the body")
            print("4. Keep the email UNREAD in Gmail")
            print("5. Run this script again")
            return "No unread emails found."
        
        print(f"📧 Found {len(emails)} unread emails to process")
        processed_emails = []
        
        for i, (email_id, msg) in enumerate(emails, 1):
            try:
                # Extract email data
                email_data = {
                    "sender": self.email_handler.get_email_sender(msg),
                    "subject": self.email_handler.get_email_subject(msg),
                    "body": self.email_handler.extract_email_body(msg),
                    "message_id": msg.get('Message-ID', ''),
                    "timestamp": datetime.now().isoformat()
                }
                
                print(f"\n[{i}/{len(emails)}] Processing email:")
                print(f"   From: {email_data['sender']}")
                print(f"   Subject: {email_data['subject'][:50]}...")
                logger.info(f"Processing email from {email_data['sender']}")
                
                # Process through workflow
                result = self.orchestrator.process_email_workflow(email_data, self.agents)
                
                # Mark as read
                self.email_handler.mark_as_read(mail, email_id)
                
                # Log result
                status = f"Email from {result['sender']}: {result['action_taken']}"
                if result['response_sent']:
                    status += " (Response sent ✓)"
                    print(f"   ✓ Action: {result['action_taken']}")
                    print(f"   ✓ Response sent")
                else:
                    print(f"   ✓ Action: {result['action_taken']}")
                    print(f"   ⚠ No response sent")
                    
                processed_emails.append(status)
                
            except Exception as e:
                logger.error(f"Error processing email: {e}")
                print(f"   ❌ Error: {str(e)}")
                processed_emails.append(f"Error processing email: {str(e)}")
        
        mail.logout()
        print("\n" + "="*60)
        print("EMAIL PROCESSING COMPLETE")
        print("="*60)
        logger.info("Email processing complete")
        
        return "\n".join(processed_emails) if processed_emails else "No emails processed"

# ============================================================================
# MAIN EXECUTION - ENHANCED WITH MORE OUTPUT
# ============================================================================

def main():
    """Main function to run the AutoGen email hiring bot with Groq and MySQL"""
    print("=" * 60)
    print("EMAIL HIRING BOT - AUTOGEN + GROQ + MYSQL")
    print("=" * 60)
    
    # Validate configuration
    if EMAIL_ADDRESS == "your-email@gmail.com":
        print("\nERROR: Please update EMAIL_ADDRESS in this script!")
        return
    
    if EMAIL_PASSWORD == "your-16-char-app-password":
        print("\nERROR: Please update EMAIL_PASSWORD in this script!")
        return
    
    if not GROQ_API_KEY or GROQ_API_KEY == "your-groq-api-key":
        print("\nERROR: Please update GROQ_API_KEY in this script!")
        print("Get your API key from: https://console.groq.com/")
        return
    
    if MYSQL_CONFIG['password'] == 'your_password':
        print("\nERROR: Please update MYSQL_CONFIG with your database credentials!")
        print("\nTo install MySQL:")
        print("- Windows: Download from https://dev.mysql.com/downloads/installer/")
        print("- Mac: Use 'brew install mysql'")
        print("- Linux: Use 'sudo apt install mysql-server'")
        return
    
    print(f"\nConfiguration:")
    print(f"Email: {EMAIL_ADDRESS}")
    print(f"Model: {GROQ_MODEL}")
    print(f"Database: {MYSQL_CONFIG['database']}@{MYSQL_CONFIG['host']}")
    print(f"Max Emails: {MAX_EMAILS_TO_PROCESS}")
    
    # Test MySQL connection
    print("\n🔌 Testing MySQL connection...")
    if not test_mysql_connection(MYSQL_CONFIG):
        print("❌ Failed to connect to MySQL. Please check your credentials.")
        print("\nMake sure MySQL is running:")
        print("- Windows: Check MySQL service in Services")
        print("- Mac/Linux: Run 'sudo service mysql status'")
        return
    print("✓ MySQL connection successful!")
    
    # Initialize database manager
    print("🗄️ Setting up database...")
    db_manager = DatabaseManager(MYSQL_CONFIG)
    print("✓ Database ready")
    
    # Test Groq connection
    print("\n🤖 Testing Groq API connection...")
    if not test_groq_connection():
        print("❌ Failed to connect to Groq API. Please check your API key.")
        return
    print("✓ Groq API connection successful!")
    
    # Initialize email handler
    email_handler = EmailHandler(
        email_address=EMAIL_ADDRESS,
        password=EMAIL_PASSWORD,
        imap_server=IMAP_SERVER,
        smtp_server=SMTP_SERVER,
        smtp_port=SMTP_PORT,
        db_manager=db_manager
    )
    
    # Test email connection
    print("\n📧 Testing email connection...")
    try:
        test_mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        test_mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        
        # Check unread count
        test_mail.select('INBOX')
        status, email_ids = test_mail.search(None, '(UNSEEN)')
        unread_count = len(email_ids[0].split()) if email_ids[0] else 0
        
        test_mail.logout()
        print(f"✓ Email connection successful!")
        print(f"📬 Unread emails in inbox: {unread_count}")
    except Exception as e:
        print(f"❌ Email connection failed: {e}")
        return
    
    # Show current system status
    show_system_status(db_manager)
    
    # Initialize and run the system
    try:
        print("\n🚀 Starting email processing...")
        print("NOTE: Jobs will require HR approval before appearing on the website")
        print("HR must reply with 'APPROVE [token]' or 'REJECT [token] [reason]'")
        print("\nThe website should query MySQL with:")
        print("SELECT * FROM tickets WHERE approval_status = 'approved'")
        
        system = EmailHiringBotSystem(email_handler, llm_config)
        result = system.process_emails()
        
        print("\n" + "=" * 60)
        print("FINAL RESULTS")
        print("=" * 60)
        print(result)
        
        # Show final status
        show_system_status(db_manager)
        
        # Optionally show detailed ticket information
        # show_ticket_details(db_manager)
        
    except KeyboardInterrupt:
        print("\n\n⚠️ Process interrupted by user.")
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# STATUS DISPLAY FUNCTIONS FOR MYSQL
# ============================================================================

def show_system_status(db_manager: DatabaseManager):
    """Display complete system status from MySQL"""
    print("\n" + "="*60)
    print("SYSTEM STATUS (MySQL)")
    print("="*60)
    
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Total tickets
            cursor.execute("SELECT COUNT(*) as total FROM tickets")
            result = cursor.fetchone()
            total_tickets = result['total'] if result else 0
            
            # Count by status - FIXED SQL
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(CASE WHEN approval_status = 'approved' THEN 1 ELSE 0 END), 0) as approved,
                    COALESCE(SUM(CASE WHEN approval_status = 'pending' THEN 1 ELSE 0 END), 0) as pending,
                    COALESCE(SUM(CASE WHEN status = 'terminated' THEN 1 ELSE 0 END), 0) as terminated
                FROM tickets
            """)
            counts = cursor.fetchone()
            
            print(f"\nTotal Tickets: {total_tickets}")
            print(f"  - Approved (Website Visible): {counts['approved'] if counts else 0}")
            print(f"  - Pending Approval: {counts['pending'] if counts else 0}")
            print(f"  - Terminated: {counts['terminated'] if counts else 0}")
            
            # Pending approval requests
            cursor.execute("""
                SELECT COUNT(*) as pending_approvals 
                FROM pending_approvals 
                WHERE status = 'pending'
            """)
            result = cursor.fetchone()
            pending_approvals = result['pending_approvals'] if result else 0
            print(f"\nPending Approval Requests: {pending_approvals}")
            
            # Show approved jobs
            print("\nJobs currently visible on website (approval_status='approved'):")
            cursor.execute("""
                SELECT t.ticket_id, td.field_value as job_title
                FROM tickets t
                LEFT JOIN ticket_details td ON t.ticket_id = td.ticket_id 
                    AND td.field_name = 'job_title' AND td.is_initial = TRUE
                WHERE t.approval_status = 'approved'
            """)
            
            approved_jobs = cursor.fetchall()
            if approved_jobs:
                for job in approved_jobs:
                    print(f"  - {job['job_title'] or 'Unknown'} (ID: {job['ticket_id']})")
            else:
                print("  - No approved jobs currently")
                
    except Exception as e:
        print(f"Error reading status: {e}")

def show_ticket_details(db_manager: DatabaseManager):
    """Display detailed information about all tickets"""
    print("\n" + "="*40)
    print("CURRENT TICKET DETAILS")
    print("="*40)
    
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # Get all tickets
            cursor.execute("""
                SELECT t.*, 
                       GROUP_CONCAT(CONCAT(td.field_name, ':', td.field_value) SEPARATOR '||') as details
                FROM tickets t
                LEFT JOIN ticket_details td ON t.ticket_id = td.ticket_id AND td.is_initial = TRUE
                GROUP BY t.ticket_id
                ORDER BY t.created_at DESC
            """)
            
            tickets = cursor.fetchall()
            
            for ticket in tickets:
                print(f"\nTicket ID: {ticket['ticket_id']}")
                print(f"Status: {ticket['status']}")
                print(f"Approval Status: {ticket['approval_status']}")
                print(f"Published to Website: {ticket['approved']}")
                print(f"Created: {ticket['created_at']}")
                print(f"Last Updated: {ticket['last_updated']}")
                
                # Parse and display details
                if ticket['details']:
                    print("Details:")
                    for detail in ticket['details'].split('||'):
                        if ':' in detail:
                            field, value = detail.split(':', 1)
                            print(f"  - {field}: {value}")
                
                # Get updates count
                cursor.execute("""
                    SELECT COUNT(*) as update_count 
                    FROM ticket_updates 
                    WHERE ticket_id = %s
                """, (ticket['ticket_id'],))
                
                update_count = cursor.fetchone()['update_count']
                if update_count > 0:
                    print(f"  Updates: {update_count} total")
                    
    except Exception as e:
        print(f"Error reading ticket details: {e}")

def test_mysql_connection(config):
    """Test MySQL connection"""
    try:
        conn = mysql.connector.connect(**config)
        if conn.is_connected():
            logger.info("MySQL connection successful!")
            conn.close()
            return True
    except Error as e:
        logger.error(f"MySQL connection failed: {e}")
        return False

def test_groq_connection():
    """Test Groq API connection"""
    try:
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "user", "content": "Say 'Groq connection successful!'"}
            ],
            "temperature": 0.1,
            "max_tokens": 50
        }
        
        response = requests.post(
            f"{GROQ_API_BASE}/chat/completions",
            headers=headers,
            json=payload,
            verify=True
        )
        
        if response.status_code == 200:
            logger.info("Groq API connection successful!")
            return True
        else:
            logger.error(f"Groq API error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error testing Groq connection: {e}")
        return False

# ============================================================================
# RUN THE MAIN PROGRAM
# ============================================================================

if __name__ == "__main__":
    main()