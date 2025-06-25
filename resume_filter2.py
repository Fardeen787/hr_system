# Enhanced AutoGen Resume Filtering System with Ticket Tracking
# All classes and dependencies in one file

import autogen
import os
import json
import PyPDF2
import docx
import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import spacy
from datetime import datetime
import pandas as pd
from pathlib import Path
import re
import hashlib
import time

# Configuration for Groq API
config_list = [
    {
        "model": "llama-3.3-70b-versatile",
        "api_key": "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",
        "base_url": "https://api.groq.com/openai/v1",
        "api_type": "openai"
    }
]

config_list_basic = [
    {
        "model": "llama-3.1-8b-instant",
        "api_key": "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",
        "base_url": "https://api.groq.com/openai/v1",
        "api_type": "openai"
    }
]


class TicketTracker:
    """Track processed tickets to avoid reprocessing"""
    
    def __init__(self, tracking_file: str = None):
        if tracking_file:
            self.tracking_file = Path(tracking_file)
        else:
            # Auto-detect tracking file location
            current_dir = Path.cwd()
            if current_dir.name == 'jobs-data':
                self.tracking_file = current_dir / '.processing_tracker.json'
            else:
                self.tracking_file = Path('jobs-data/.processing_tracker.json')
        
        self.processed_tickets = self._load_tracking_data()
    
    def _load_tracking_data(self) -> Dict:
        """Load tracking data from file"""
        if self.tracking_file.exists():
            try:
                with open(self.tracking_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_tracking_data(self):
        """Save tracking data to file"""
        # Ensure directory exists
        self.tracking_file.parent.mkdir(exist_ok=True)
        with open(self.tracking_file, 'w') as f:
            json.dump(self.processed_tickets, f, indent=2)
    
    def get_ticket_hash(self, ticket_folder: Path) -> str:
        """Generate a hash based on ticket content to detect changes"""
        hash_content = []
        
        # Include job details/data files
        for pattern in ['job-data.json', 'job_details.json', '*.json']:
            for json_file in ticket_folder.glob(pattern):
                if json_file.name != 'applications.json' and json_file.exists():
                    with open(json_file, 'r') as f:
                        hash_content.append(f.read())
                    break
        
        # Include number and names of resume files
        resume_files = []
        for ext in ['.pdf', '.docx', '.doc']:
            resume_files.extend([f.name for f in ticket_folder.glob(f"*{ext}")])
        
        resume_files.sort()
        hash_content.append(','.join(resume_files))
        
        # Generate hash
        content_str = ''.join(hash_content)
        return hashlib.md5(content_str.encode()).hexdigest()
    
    def is_ticket_processed(self, ticket_folder: Path) -> Tuple[bool, Optional[str]]:
        """Check if ticket has been processed and if content changed"""
        ticket_id = ticket_folder.name
        current_hash = self.get_ticket_hash(ticket_folder)
        
        if ticket_id in self.processed_tickets:
            stored_data = self.processed_tickets[ticket_id]
            stored_hash = stored_data.get('content_hash', '')
            
            if stored_hash == current_hash:
                return True, stored_data.get('last_processed')
            else:
                return False, "content_changed"
        
        return False, None
    
    def mark_ticket_processed(self, ticket_folder: Path, results_file: str):
        """Mark ticket as processed"""
        ticket_id = ticket_folder.name
        current_hash = self.get_ticket_hash(ticket_folder)
        
        self.processed_tickets[ticket_id] = {
            'content_hash': current_hash,
            'last_processed': datetime.now().isoformat(),
            'results_file': results_file,
            'status': 'completed'
        }
        
        self._save_tracking_data()
    
    def get_processing_summary(self) -> Dict:
        """Get summary of all processed tickets"""
        return {
            'total_processed': len(self.processed_tickets),
            'tickets': self.processed_tickets
        }
    
    def reset_ticket(self, ticket_id: str):
        """Reset a specific ticket to allow reprocessing"""
        if ticket_id in self.processed_tickets:
            del self.processed_tickets[ticket_id]
            self._save_tracking_data()
            return True
        return False
    
    def reset_all(self):
        """Reset all tracking data"""
        self.processed_tickets = {}
        self._save_tracking_data()


class BatchProcessor:
    """Process multiple job tickets in batch"""
    
    def __init__(self, jobs_folder: str = None):
        # Determine the jobs folder
        if jobs_folder:
            self.jobs_folder = Path(jobs_folder)
        else:
            # Check if we're already in jobs-data folder
            current_dir = Path.cwd()
            if current_dir.name == 'jobs-data':
                self.jobs_folder = current_dir
            else:
                self.jobs_folder = current_dir / 'jobs-data'
        
        # Adjust tracking file path based on current location
        if self.jobs_folder.name == 'jobs-data':
            tracking_file = self.jobs_folder / '.processing_tracker.json'
        else:
            tracking_file = 'jobs-data/.processing_tracker.json'
        
        self.tracker = TicketTracker(str(tracking_file))
        self.results_summary = []
        
        # Create batch results folder
        self.batch_results_folder = self.jobs_folder / "batch_results"
        self.batch_results_folder.mkdir(exist_ok=True, parents=True)
    
    def get_all_tickets(self) -> List[Path]:
        """Get all ticket folders in jobs-data"""
        tickets = []
        
        # Look for directories that contain job data
        for item in self.jobs_folder.iterdir():
            if item.is_dir() and not item.name.startswith('.') and item.name != 'batch_results':
                # Check if it contains job data files
                has_job_data = any([
                    (item / 'job-data.json').exists(),
                    (item / 'job_details.json').exists(),
                    (item / 'applications.json').exists()
                ])
                
                if has_job_data:
                    tickets.append(item)
        
        return sorted(tickets)
    
    def process_all_tickets(self, force_reprocess: bool = False, specific_tickets: List[str] = None):
        """Process all tickets in the jobs folder"""
        print(f"\n{'='*80}")
        print(f"🚀 BATCH RESUME FILTERING SYSTEM")
        print(f"{'='*80}")
        
        all_tickets = self.get_all_tickets()
        
        if specific_tickets:
            # Filter to only specified tickets
            all_tickets = [t for t in all_tickets if t.name in specific_tickets]
        
        print(f"📁 Found {len(all_tickets)} ticket(s) in {self.jobs_folder}")
        
        if not all_tickets:
            print("❌ No valid job tickets found!")
            return
        
        # Summary tracking
        processed_count = 0
        skipped_count = 0
        error_count = 0
        
        # Process each ticket
        for i, ticket_folder in enumerate(all_tickets, 1):
            print(f"\n{'='*70}")
            print(f"📋 Processing Ticket {i}/{len(all_tickets)}: {ticket_folder.name}")
            print(f"{'='*70}")
            
            try:
                # Check if already processed
                is_processed, process_info = self.tracker.is_ticket_processed(ticket_folder)
                
                if is_processed and not force_reprocess:
                    print(f"✅ Already processed on: {process_info}")
                    print(f"   (Use --force to reprocess)")
                    skipped_count += 1
                    
                    # Load previous results for summary
                    self._add_to_summary(ticket_folder, "skipped", process_info)
                    continue
                
                if process_info == "content_changed":
                    print(f"🔄 Content changed since last processing. Reprocessing...")
                
                # Process the ticket
                print(f"🔍 Starting resume filtering for: {ticket_folder.name}")
                
                filter_system = UpdatedResumeFilteringSystem(str(ticket_folder))
                results = filter_system.filter_resumes()
                
                if "error" not in results:
                    # Mark as processed
                    results_file = filter_system.output_folder / f"final_results_{filter_system.job_ticket.ticket_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    self.tracker.mark_ticket_processed(ticket_folder, str(results_file))
                    
                    processed_count += 1
                    self._add_to_summary(ticket_folder, "completed", results)
                    
                    print(f"\n✅ Successfully processed ticket: {ticket_folder.name}")
                else:
                    error_count += 1
                    self._add_to_summary(ticket_folder, "error", results.get("error"))
                    print(f"\n❌ Error processing ticket: {results.get('error')}")
                
            except Exception as e:
                error_count += 1
                self._add_to_summary(ticket_folder, "error", str(e))
                print(f"\n❌ Error processing ticket {ticket_folder.name}: {str(e)}")
                import traceback
                traceback.print_exc()
            
            # Add delay between tickets to avoid API rate limits
            if i < len(all_tickets):
                print(f"\n⏳ Waiting 2 seconds before next ticket...")
                time.sleep(2)
        
        # Generate batch summary report
        self._generate_batch_summary(processed_count, skipped_count, error_count)
    
    def _add_to_summary(self, ticket_folder: Path, status: str, data: Any):
        """Add ticket result to summary"""
        summary_entry = {
            'ticket_id': ticket_folder.name,
            'ticket_path': str(ticket_folder),
            'status': status,
            'timestamp': datetime.now().isoformat()
        }
        
        if status == "completed" and isinstance(data, dict):
            summary_entry.update({
                'position': data.get('position', 'Unknown'),
                'total_resumes': data.get('summary', {}).get('total_resumes', 0),
                'top_5_selected': len(data.get('final_top_5', [])),
                'top_candidates': [
                    {
                        'name': c['filename'],
                        'score': f"{c['final_score']:.1%}"
                    }
                    for c in data.get('final_top_5', [])[:3]  # Top 3 for summary
                ]
            })
        elif status == "error":
            summary_entry['error_message'] = str(data)
        elif status == "skipped":
            summary_entry['last_processed'] = data
        
        self.results_summary.append(summary_entry)
    
    def _generate_batch_summary(self, processed: int, skipped: int, errors: int):
        """Generate comprehensive batch processing summary"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # JSON summary
        json_summary = {
            'batch_run_timestamp': datetime.now().isoformat(),
            'statistics': {
                'total_tickets': processed + skipped + errors,
                'processed': processed,
                'skipped': skipped,
                'errors': errors
            },
            'tickets': self.results_summary,
            'tracker_summary': self.tracker.get_processing_summary()
        }
        
        json_file = self.batch_results_folder / f"batch_summary_{timestamp}.json"
        with open(json_file, 'w') as f:
            json.dump(json_summary, f, indent=2)
        
        # Text report
        report_file = self.batch_results_folder / f"batch_report_{timestamp}.txt"
        with open(report_file, 'w') as f:
            f.write(f"BATCH PROCESSING SUMMARY REPORT\n")
            f.write(f"{'='*80}\n")
            f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"\nSTATISTICS:\n")
            f.write(f"  Total Tickets: {processed + skipped + errors}\n")
            f.write(f"  Processed: {processed}\n")
            f.write(f"  Skipped (Already Processed): {skipped}\n")
            f.write(f"  Errors: {errors}\n")
            
            f.write(f"\n{'='*80}\n")
            f.write(f"TICKET DETAILS:\n")
            f.write(f"{'='*80}\n\n")
            
            for ticket in self.results_summary:
                f.write(f"Ticket ID: {ticket['ticket_id']}\n")
                f.write(f"Status: {ticket['status'].upper()}\n")
                
                if ticket['status'] == 'completed':
                    f.write(f"Position: {ticket.get('position', 'Unknown')}\n")
                    f.write(f"Resumes Processed: {ticket.get('total_resumes', 0)}\n")
                    f.write(f"Top Candidates:\n")
                    for j, candidate in enumerate(ticket.get('top_candidates', []), 1):
                        f.write(f"  {j}. {candidate['name']} - {candidate['score']}\n")
                elif ticket['status'] == 'skipped':
                    f.write(f"Last Processed: {ticket.get('last_processed', 'Unknown')}\n")
                elif ticket['status'] == 'error':
                    f.write(f"Error: {ticket.get('error_message', 'Unknown error')}\n")
                
                f.write(f"\n{'-'*50}\n\n")
        
        # Print summary to console
        print(f"\n{'='*80}")
        print(f"📊 BATCH PROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"Total Tickets: {processed + skipped + errors}")
        print(f"✅ Processed: {processed}")
        print(f"⏭️  Skipped: {skipped}")
        print(f"❌ Errors: {errors}")
        print(f"\n📁 Batch results saved to: {self.batch_results_folder}")
        print(f"   - Summary: {json_file.name}")
        print(f"   - Report: {report_file.name}")
    
    def show_status(self):
        """Show status of all tickets"""
        all_tickets = self.get_all_tickets()
        tracking_data = self.tracker.get_processing_summary()
        
        print(f"\n{'='*80}")
        print(f"📊 JOB TICKETS STATUS")
        print(f"{'='*80}")
        print(f"Total Tickets Found: {len(all_tickets)}")
        print(f"Total Processed: {tracking_data['total_processed']}")
        print(f"\nTicket Details:")
        print(f"{'-'*80}")
        
        for ticket in all_tickets:
            ticket_id = ticket.name
            status = "❌ Not Processed"
            last_processed = "Never"
            
            if ticket_id in tracking_data['tickets']:
                status = "✅ Processed"
                last_processed = tracking_data['tickets'][ticket_id]['last_processed']
            
            print(f"{ticket_id:30} | {status:20} | Last: {last_processed}")


# Keep all the original classes from your code here
# (EnhancedJobTicket, ResumeExtractor, UpdateAwareResumeFilter, 
#  UpdateAwareBasicFilter, UpdatedResumeFilteringSystem)
# ... [Include all the original classes without modification] ...

class EnhancedJobTicket:
    """Enhanced JobTicket class that reads latest updates from JSON structure"""
    
    def __init__(self, ticket_folder: str):
        self.ticket_folder = Path(ticket_folder)
        self.ticket_id = self.ticket_folder.name
        self.raw_data = self._load_raw_data()
        self.job_details = self._merge_with_updates()
        self._print_loaded_details()
    
    def _load_raw_data(self) -> Dict:
        """Load the raw JSON data from the ticket folder"""
        # Look for job-data.json first, then job_details.json, then any JSON
        priority_files = ['job-data.json', 'job_details.json', 'job.json']
        json_file = None
        
        for filename in priority_files:
            file_path = self.ticket_folder / filename
            if file_path.exists():
                json_file = file_path
                break
        
        # If no priority file found, look for any JSON except applications.json
        if not json_file:
            json_files = [f for f in self.ticket_folder.glob("*.json") 
                         if f.name != 'applications.json']
            if json_files:
                json_file = json_files[0]
        
        if not json_file:
            # If only applications.json exists, use it as fallback
            app_file = self.ticket_folder / 'applications.json'
            if app_file.exists():
                json_file = app_file
            else:
                raise FileNotFoundError(f"No JSON file found in {self.ticket_folder}")
        
        print(f"📄 Loading job details from: {json_file.name}")
        
        # Load job description from txt file if exists
        job_desc_file = self.ticket_folder / 'job-description.txt'
        job_description_text = ""
        if job_desc_file.exists():
            print(f"📝 Loading job description from: job-description.txt")
            with open(job_desc_file, 'r', encoding='utf-8') as f:
                job_description_text = f.read()
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # If we loaded job description separately, add it to the data
            if job_description_text and isinstance(data, dict):
                if 'job_description' not in data:
                    data['job_description'] = job_description_text
                if 'initial_details' in data and 'job_description' not in data['initial_details']:
                    data['initial_details']['job_description'] = job_description_text
            
            return data
        except Exception as e:
            print(f"❌ Error loading JSON: {e}")
            raise
    
    def _merge_with_updates(self) -> Dict:
        """Merge initial details with latest updates"""
        # Handle different JSON structures
        if isinstance(self.raw_data, list):
            # If it's a list of applications, create a job details structure
            print("📝 Detected applications list format, creating job structure...")
            merged_details = {
                'ticket_id': self.ticket_id,
                'applications': self.raw_data,
                'status': 'active',
                'created_at': datetime.now().isoformat(),
                'last_updated': datetime.now().isoformat(),
                # Default job details - you may need to adjust these
                'job_title': 'Software Developer',
                'position': 'Software Developer',
                'experience_required': '2+ years',
                'location': 'Remote',
                'salary_range': 'Competitive',
                'required_skills': 'Python, JavaScript, SQL',
                'job_description': 'We are looking for a talented developer',
                'deadline': 'Open until filled'
            }
            return merged_details
        
        # Original logic for dictionary format
        if 'initial_details' in self.raw_data:
            merged_details = self.raw_data['initial_details'].copy()
        else:
            merged_details = self.raw_data.copy()
        
        merged_details['ticket_id'] = self.raw_data.get('ticket_id', self.ticket_id)
        merged_details['status'] = self.raw_data.get('status', 'unknown')
        merged_details['created_at'] = self.raw_data.get('created_at', '')
        merged_details['last_updated'] = self.raw_data.get('last_updated', '')
        
        if 'updates' in self.raw_data and self.raw_data['updates']:
            print(f"📝 Found {len(self.raw_data['updates'])} update(s)")
            
            sorted_updates = sorted(
                self.raw_data['updates'], 
                key=lambda x: x.get('timestamp', ''),
                reverse=True
            )
            
            latest_update = sorted_updates[0]
            print(f"✅ Applying latest update from: {latest_update.get('timestamp', 'unknown')}")
            
            if 'details' in latest_update:
                for key, value in latest_update['details'].items():
                    if value:
                        merged_details[key] = value
                        print(f"   Updated {key}: {value}")
        
        return merged_details
    
    def _print_loaded_details(self):
        """Print the loaded job details for verification"""
        print("\n" + "="*60)
        print("📋 LOADED JOB REQUIREMENTS (WITH LATEST UPDATES)")
        print("="*60)
        print(f"Position: {self.position}")
        print(f"Experience: {self.experience_required}")
        print(f"Location: {self.location}")
        print(f"Salary: {self.salary_range}")
        print(f"Skills: {', '.join(self.tech_stack)}")
        print(f"Deadline: {self.deadline}")
        print(f"Last Updated: {self.job_details.get('last_updated', 'Unknown')}")
        print("="*60 + "\n")
    
    def _parse_skills(self, skills_str: str) -> List[str]:
        """Parse skills from string format to list"""
        if isinstance(skills_str, list):
            return skills_str
        
        if not skills_str:
            return []
        
        skills = re.split(r'[,;|]\s*', skills_str)
        expanded_skills = []
        
        for skill in skills:
            if '(' in skill and ')' in skill:
                main_skill = skill[:skill.index('(')].strip()
                variations = skill[skill.index('(')+1:skill.index(')')].strip()
                expanded_skills.append(main_skill)
                if '/' in variations:
                    expanded_skills.extend([v.strip() for v in variations.split('/')])
                else:
                    expanded_skills.append(variations)
            else:
                expanded_skills.append(skill.strip())
        
        return list(set([s for s in expanded_skills if s]))
    
    @property
    def position(self) -> str:
        return (self.job_details.get('job_title') or 
                self.job_details.get('position') or 
                self.job_details.get('title', 'Unknown Position'))
    
    @property
    def experience_required(self) -> str:
        return (self.job_details.get('experience_required') or 
                self.job_details.get('experience') or 
                self.job_details.get('years_of_experience', '0+ years'))
    
    @property
    def location(self) -> str:
        return self.job_details.get('location', 'Not specified')
    
    @property
    def salary_range(self) -> str:
        salary = self.job_details.get('salary_range', '')
        if isinstance(salary, dict):
            min_sal = salary.get('min', '')
            max_sal = salary.get('max', '')
            currency = salary.get('currency', 'INR')
            return f"{currency} {min_sal}-{max_sal}"
        return salary or 'Not specified'
    
    @property
    def deadline(self) -> str:
        return self.job_details.get('deadline', 'Not specified')
    
    @property
    def tech_stack(self) -> List[str]:
        skills = self.job_details.get('required_skills') or self.job_details.get('tech_stack', '')
        return self._parse_skills(skills)
    
    @property
    def requirements(self) -> List[str]:
        requirements = []
        
        if self.job_details.get('job_description'):
            requirements.append(self.job_details['job_description'])
        
        req_field = self.job_details.get('requirements', [])
        if isinstance(req_field, str):
            requirements.extend([r.strip() for r in req_field.split('\n') if r.strip()])
        elif isinstance(req_field, list):
            requirements.extend(req_field)
        
        return requirements
    
    @property
    def description(self) -> str:
        return (self.job_details.get('job_description') or 
                self.job_details.get('description') or 
                self.job_details.get('summary', ''))
    
    @property
    def employment_type(self) -> str:
        return self.job_details.get('employment_type', 'Full-time')
    
    @property
    def nice_to_have(self) -> List[str]:
        nice = (self.job_details.get('nice_to_have') or 
                self.job_details.get('preferred_skills') or 
                self.job_details.get('bonus_skills', []))
        
        if isinstance(nice, str):
            return [n.strip() for n in nice.split('\n') if n.strip()]
        elif isinstance(nice, list):
            return nice
        return []
    
    def get_resumes(self) -> List[Path]:
        """Get all resume files from the ticket folder"""
        resume_extensions = ['.pdf', '.docx', '.doc']  # Removed .txt to avoid job descriptions
        resumes = []
        
        for ext in resume_extensions:
            resumes.extend(self.ticket_folder.glob(f"*{ext}"))
        
        # Expanded exclusion list
        excluded_keywords = ['job_description', 'job-description', 'requirements', 'jd', 'job_posting', 'job-posting']
        filtered_resumes = []
        
        for resume in resumes:
            # Check if file name contains any excluded keyword
            if not any(keyword in resume.name.lower().replace('_', '-') for keyword in excluded_keywords):
                filtered_resumes.append(resume)
            else:
                print(f"   ℹ️ Excluding non-resume file: {resume.name}")
        
        return filtered_resumes


class ResumeExtractor:
    """Extract text from various resume formats"""
    
    @staticmethod
    def extract_text_from_pdf(file_path: str) -> str:
        """Extract text from PDF file"""
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text() + "\n"
                return text
        except Exception as e:
            print(f"Error reading PDF {file_path}: {e}")
            return ""
    
    @staticmethod
    def extract_text_from_docx(file_path: str) -> str:
        """Extract text from DOCX file"""
        try:
            doc = docx.Document(file_path)
            text = "\n".join([paragraph.text for paragraph in doc.paragraphs])
            return text
        except Exception as e:
            print(f"Error reading DOCX {file_path}: {e}")
            return ""
    
    @staticmethod
    def extract_text(file_path: Path) -> str:
        """Extract text from resume file"""
        file_path_str = str(file_path)
        
        if file_path.suffix.lower() == '.pdf':
            return ResumeExtractor.extract_text_from_pdf(file_path_str)
        elif file_path.suffix.lower() in ['.docx', '.doc']:
            return ResumeExtractor.extract_text_from_docx(file_path_str)
        elif file_path.suffix.lower() == '.txt':
            with open(file_path_str, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            return ""


class UpdateAwareResumeFilter:
    """Resume filter that considers updated job requirements"""
    
    def __init__(self):
        self.skill_variations = self._build_skill_variations()
    
    def _build_skill_variations(self) -> Dict[str, List[str]]:
        """Build comprehensive skill variations dictionary"""
        return {
            # Programming Languages
            "python": ["python", "py", "python3", "python2", "python 3", "python 2"],
            "javascript": ["javascript", "js", "node.js", "nodejs", "node", "ecmascript", "es6", "es5"],
            "java": ["java", "jvm", "j2ee", "java8", "java11", "java17"],
            "c++": ["c++", "cpp", "cplusplus", "c plus plus"],
            "c#": ["c#", "csharp", "c sharp", ".net", "dotnet"],
            
            # Databases
            "sql": ["sql", "structured query language", "tsql", "t-sql", "plsql", "pl/sql"],
            "mongodb": ["mongodb", "mongo", "mongod", "nosql mongodb"],
            "redis": ["redis", "redis cache", "redis db", "redis database"],
            "postgresql": ["postgresql", "postgres", "pgsql", "postgre"],
            "mysql": ["mysql", "my sql", "mariadb"],
            
            # Frameworks
            "react": ["react", "reactjs", "react.js", "react js", "react native"],
            "angular": ["angular", "angularjs", "angular.js", "angular js"],
            "django": ["django", "django rest", "drf", "django framework"],
            "spring": ["spring", "spring boot", "springboot", "spring framework"],
            "flask": ["flask", "flask api", "flask framework"],
            
            # Cloud Platforms
            "aws": ["aws", "amazon web services", "ec2", "s3", "lambda", "amazon aws"],
            "gcp": ["gcp", "google cloud", "google cloud platform", "gcloud"],
            "azure": ["azure", "microsoft azure", "ms azure", "windows azure"],
            
            # Big Data
            "spark": ["spark", "apache spark", "pyspark", "spark sql"],
            "hadoop": ["hadoop", "hdfs", "mapreduce", "apache hadoop"],
            "kafka": ["kafka", "apache kafka", "kafka streams"],
            
            # Machine Learning
            "machine learning": ["machine learning", "ml", "scikit-learn", "sklearn", "ml models"],
            "deep learning": ["deep learning", "dl", "neural networks", "nn", "dnn"],
            "tensorflow": ["tensorflow", "tf", "tf2", "tensorflow 2"],
            "pytorch": ["pytorch", "torch", "py torch"],
            
            # Others
            "docker": ["docker", "containers", "containerization", "dockerfile"],
            "kubernetes": ["kubernetes", "k8s", "kubectl", "k8", "container orchestration"],
            "graphql": ["graphql", "graph ql", "apollo", "graphql api"],
            "rest": ["rest", "restful", "rest api", "restful api", "rest services"],
            "git": ["git", "github", "gitlab", "bitbucket", "version control", "vcs"],
            "ci/cd": ["ci/cd", "cicd", "continuous integration", "continuous deployment", "jenkins", "travis", "circle ci"],
            "agile": ["agile", "scrum", "kanban", "sprint", "agile methodology"],
        }
    
    def calculate_skill_match_score(self, resume_text: str, required_skills: List[str]) -> tuple[float, List[str], Dict[str, List[str]]]:
        """Calculate skill matching score with variations"""
        resume_lower = resume_text.lower()
        matched_skills = []
        detailed_matches = {}
        
        for skill in required_skills:
            skill_lower = skill.lower().strip()
            skill_matched = False
            
            if skill_lower in resume_lower:
                matched_skills.append(skill)
                detailed_matches[skill] = [skill_lower]
                skill_matched = True
                continue
            
            skill_key = None
            for key in self.skill_variations:
                if skill_lower in self.skill_variations[key] or key in skill_lower:
                    skill_key = key
                    break
            
            if skill_key and skill_key in self.skill_variations:
                variations_found = []
                for variation in self.skill_variations[skill_key]:
                    if variation in resume_lower:
                        variations_found.append(variation)
                        skill_matched = True
                
                if variations_found:
                    matched_skills.append(skill)
                    detailed_matches[skill] = variations_found
            
            if not skill_matched and ' ' in skill:
                parts = skill.split()
                if all(part.lower() in resume_lower for part in parts):
                    matched_skills.append(skill)
                    detailed_matches[skill] = [skill_lower]
        
        score = len(matched_skills) / len(required_skills) if required_skills else 0
        return score, matched_skills, detailed_matches
    
    def parse_experience_range(self, experience_str: str) -> tuple[int, int]:
        """Parse experience range like '5-8 years' to (5, 8)"""
        numbers = re.findall(r'\d+', experience_str)
        
        if len(numbers) >= 2:
            return int(numbers[0]), int(numbers[1])
        elif len(numbers) == 1:
            if '+' in experience_str:
                return int(numbers[0]), int(numbers[0]) + 5
            else:
                return int(numbers[0]), int(numbers[0])
        else:
            return 0, 100
    
    def calculate_experience_match(self, resume_text: str, required_experience: str) -> tuple[float, int]:
        """Calculate experience matching score"""
        min_req, max_req = self.parse_experience_range(required_experience)
        
        patterns = [
            r'(\d+)\+?\s*years?\s*(?:of\s*)?(?:professional\s*)?experience',
            r'experience\s*[:–-]\s*(\d+)\+?\s*years?',
            r'(\d+)\+?\s*years?\s*in\s*(?:software|data|engineering|development)',
            r'total\s*experience\s*[:–-]\s*(\d+)\+?\s*years?',
            r'(\d+)\+?\s*yrs?\s*exp',
            r'(\d{4})\s*[-–]\s*(?:present|current|now|(\d{4}))',
        ]
        
        years_found = []
        
        for pattern in patterns:
            matches = re.findall(pattern, resume_text.lower())
            for match in matches:
                if isinstance(match, tuple):
                    if match[0].isdigit() and len(match[0]) == 4:
                        start_year = int(match[0])
                        if match[1] and match[1].isdigit():
                            end_year = int(match[1])
                        else:
                            end_year = datetime.now().year
                        
                        if start_year > 1990:
                            years_found.append(end_year - start_year)
                else:
                    if match.isdigit():
                        years_found.append(int(match))
        
        if years_found:
            candidate_years = max(years_found)
            
            if min_req <= candidate_years <= max_req:
                return 1.0, candidate_years
            elif candidate_years > max_req:
                return 0.9, candidate_years
            elif candidate_years >= min_req - 1:
                return 0.8, candidate_years
            else:
                return candidate_years / min_req if min_req > 0 else 0, candidate_years
        
        return 0.0, 0
    
    def score_resume(self, resume_text: str, job_ticket: EnhancedJobTicket) -> Dict[str, Any]:
        """Score a resume against job requirements"""
        skill_score, matched_skills, detailed_matches = self.calculate_skill_match_score(
            resume_text, job_ticket.tech_stack
        )
        
        exp_score, detected_years = self.calculate_experience_match(
            resume_text, job_ticket.experience_required
        )
        
        location_score = 0.0
        if job_ticket.location.lower() in resume_text.lower():
            location_score = 1.0
        elif "remote" in job_ticket.location.lower() or "remote" in resume_text.lower():
            location_score = 0.8
        
        weights = {
            'skills': 0.50,
            'experience': 0.35,
            'location': 0.15
        }
        
        final_score = (
            weights['skills'] * skill_score +
            weights['experience'] * exp_score +
            weights['location'] * location_score
        )
        
        return {
            'final_score': final_score,
            'skill_score': skill_score,
            'experience_score': exp_score,
            'location_score': location_score,
            'matched_skills': matched_skills,
            'detailed_skill_matches': detailed_matches,
            'detected_experience_years': detected_years,
            'scoring_weights': weights,
            'job_requirements': {
                'position': job_ticket.position,
                'required_skills': job_ticket.tech_stack,
                'required_experience': job_ticket.experience_required,
                'location': job_ticket.location
            }
        }


class UpdateAwareBasicFilter:
    """Enhanced basic filter with comprehensive scoring"""
    
    def __init__(self):
        self.resume_filter = UpdateAwareResumeFilter()
        
        try:
            self.nlp = spacy.load("en_core_web_sm")
        except:
            os.system("python -m spacy download en_core_web_sm")
            self.nlp = spacy.load("en_core_web_sm")
        
        self.vectorizer = TfidfVectorizer(
            max_features=500,
            stop_words='english',
            ngram_range=(1, 2)
        )
    
    def score_resume_comprehensive(self, resume_text: str, resume_path: Path, job_ticket: EnhancedJobTicket) -> Dict:
        """Comprehensive scoring using multiple methods"""
        base_scores = self.resume_filter.score_resume(resume_text, job_ticket)
        
        similarity_score = 0.0
        if job_ticket.description:
            try:
                tfidf_matrix = self.vectorizer.fit_transform([job_ticket.description, resume_text])
                similarity_score = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
            except:
                similarity_score = 0.0
        
        additional_features = self._extract_additional_features(resume_text)
        
        result = {
            "file_path": str(resume_path),
            "filename": resume_path.name,
            "final_score": base_scores['final_score'],
            "skill_score": base_scores['skill_score'],
            "experience_score": base_scores['experience_score'],
            "location_score": base_scores['location_score'],
            "similarity_score": similarity_score,
            "matched_skills": base_scores['matched_skills'],
            "detailed_skill_matches": base_scores['detailed_skill_matches'],
            "detected_experience_years": base_scores['detected_experience_years'],
            "additional_features": additional_features,
            "scoring_weights": base_scores['scoring_weights'],
            "job_requirements_used": base_scores['job_requirements']
        }
        
        return result
    
    def _extract_additional_features(self, resume_text: str) -> Dict:
        """Extract additional features from resume"""
        features = {}
        
        education_keywords = {
            'phd': 4, 'doctorate': 4,
            'master': 3, 'mba': 3, 'ms': 3, 'mtech': 3,
            'bachelor': 2, 'btech': 2, 'bs': 2, 'be': 2,
            'diploma': 1
        }
        
        resume_lower = resume_text.lower()
        education_score = 0
        for keyword, score in education_keywords.items():
            if keyword in resume_lower:
                education_score = max(education_score, score)
        
        features['education_level'] = education_score
        
        cert_keywords = ['certified', 'certification', 'certificate', 'aws certified', 'google certified', 'microsoft certified']
        features['has_certifications'] = any(cert in resume_lower for cert in cert_keywords)
        
        leadership_keywords = ['lead', 'manager', 'head', 'director', 'principal', 'senior', 'architect']
        features['leadership_experience'] = sum(1 for keyword in leadership_keywords if keyword in resume_lower)
        
        return features


class UpdatedResumeFilteringSystem:
    """Complete resume filtering system with update support"""
    
    def __init__(self, ticket_folder: str):
        self.ticket_folder = Path(ticket_folder)
        self.job_ticket = EnhancedJobTicket(ticket_folder)
        self.basic_filter = UpdateAwareBasicFilter()
        
        self.output_folder = self.ticket_folder / "filtering_results"
        self.output_folder.mkdir(exist_ok=True)
        
        self._create_agents()
    
    def _create_agents(self):
        """Create AutoGen agents with latest job requirements"""
        latest_skills = ', '.join(self.job_ticket.tech_stack)
        latest_experience = self.job_ticket.experience_required
        latest_salary = self.job_ticket.salary_range
        
        llm_config = {
            "config_list": config_list,
            "temperature": 0.2,
            "timeout": 60,
            "cache_seed": 42,
        }
        
        llm_config_basic = {
            "config_list": config_list_basic,
            "temperature": 0.1,
            "timeout": 30,
            "cache_seed": 42,
        }
        
        self.basic_filter_agent = autogen.AssistantAgent(
            name="basic_filter_agent",
            llm_config=llm_config_basic,
            system_message=f"""You are a resume screening assistant for: {self.job_ticket.position}
            
            LATEST JOB REQUIREMENTS (Updated: {self.job_ticket.job_details.get('last_updated', 'Unknown')}):
            - Experience: {latest_experience}
            - Required Skills: {latest_skills}
            - Location: {self.job_ticket.location}
            - Salary: {latest_salary}
            - Deadline: {self.job_ticket.deadline}
            
            Review resume scores and validate selections based on LATEST requirements.
            Flag any candidates who don't meet the updated criteria.
            """
        )
        
        self.advanced_filter_agent = autogen.AssistantAgent(
            name="advanced_filter_agent",
            llm_config=llm_config,
            system_message=f"""You are an expert technical recruiter evaluating for: {self.job_ticket.position}
            
            CRITICAL - USE LATEST REQUIREMENTS:
            - Experience Range: {latest_experience}
            - Must-Have Skills: {latest_skills}
            - Location: {self.job_ticket.location}
            - Salary Budget: {latest_salary}
            
            Select the BEST 5 candidates who meet the UPDATED requirements.
            Consider the latest skill requirements especially: {latest_skills}
            
            Rank 1-5 based on fit with updated requirements.
            """
        )
        
        self.qa_agent = autogen.AssistantAgent(
            name="qa_agent",
            llm_config=llm_config,
            system_message=f"""Quality assurance specialist reviewing selections.
            
            Ensure all selections meet the LATEST requirements:
            - Updated Skills: {latest_skills}
            - Updated Experience: {latest_experience}
            - Updated Salary: {latest_salary}
            
            Verify no outdated requirements were used in scoring.
            """
        )
        
        self.user_proxy = autogen.UserProxyAgent(
            name="user_proxy",
            human_input_mode="NEVER",
            max_consecutive_auto_reply=0,
            code_execution_config={
                "work_dir": str(self.output_folder),
                "use_docker": False,
            }
        )
    
    def filter_resumes(self) -> Dict:
        """Main filtering method with update awareness"""
        print(f"\n{'='*70}")
        print(f"🚀 RESUME FILTERING SYSTEM - WITH LATEST UPDATES")
        print(f"{'='*70}")
        print(f"Job Ticket: {self.job_ticket.ticket_id}")
        print(f"Position: {self.job_ticket.position}")
        print(f"Status: {self.job_ticket.job_details.get('status', 'Unknown')}")
        print(f"Last Updated: {self.job_ticket.job_details.get('last_updated', 'Unknown')}")
        print(f"\n📋 USING LATEST REQUIREMENTS:")
        print(f"  • Experience: {self.job_ticket.experience_required}")
        print(f"  • Skills: {', '.join(self.job_ticket.tech_stack)}")
        print(f"  • Location: {self.job_ticket.location}")
        print(f"  • Salary: {self.job_ticket.salary_range}")
        print(f"  • Deadline: {self.job_ticket.deadline}")
        print(f"{'='*70}\n")
        
        resumes = self.job_ticket.get_resumes()
        print(f"📄 Found {len(resumes)} resumes to process")
        
        if not resumes:
            return {
                "error": "No resumes found in the ticket folder",
                "ticket_id": self.job_ticket.ticket_id
            }
        
        print("\n🔍 Stage 1: Basic AI Filtering (Using Latest Requirements)...")
        initial_results = self._basic_filtering_updated(resumes)
        
        with open(self.output_folder / "stage1_results.json", 'w') as f:
            json.dump(initial_results, f, indent=2, default=str)
        
        print("\n🧠 Stage 2: Advanced LLM Analysis...")
        final_results = self._advanced_filtering(initial_results)
        
        with open(self.output_folder / "stage2_results.json", 'w') as f:
            json.dump(final_results, f, indent=2, default=str)
        
        print("\n✅ Stage 3: Quality Assurance Review...")
        qa_results = self._quality_assurance(initial_results, final_results)
        
        final_output = {
            "ticket_id": self.job_ticket.ticket_id,
            "position": self.job_ticket.position,
            "timestamp": datetime.now().isoformat(),
            "job_status": self.job_ticket.job_details.get('status', 'unknown'),
            "requirements_last_updated": self.job_ticket.job_details.get('last_updated', ''),
            "latest_requirements": {
                "experience": self.job_ticket.experience_required,
                "tech_stack": self.job_ticket.tech_stack,
                "location": self.job_ticket.location,
                "salary": self.job_ticket.salary_range,
                "deadline": self.job_ticket.deadline
            },
            "summary": {
                "total_resumes": len(resumes),
                "stage1_selected": len(initial_results["top_10"]),
                "final_selected": len(final_results.get("top_5_candidates", [])),
            },
            "stage1_results": initial_results,
            "stage2_results": final_results,
            "qa_review": qa_results,
            "final_top_5": final_results.get("top_5_candidates", []),
        }
        
        output_file = self.output_folder / f"final_results_{self.job_ticket.ticket_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w') as f:
            json.dump(final_output, f, indent=2, default=str)
        
        self._create_enhanced_summary_report(final_output)
        
        print(f"\n✅ Filtering complete! Results saved to: {output_file}")
        
        return final_output
    
    def _basic_filtering_updated(self, resumes: List[Path]) -> Dict:
        """Stage 1 with update-aware scoring"""
        scored_resumes = []
        
        for i, resume_path in enumerate(resumes):
            print(f"  Processing {i+1}/{len(resumes)}: {resume_path.name}")
            
            resume_text = ResumeExtractor.extract_text(resume_path)
            
            if not resume_text:
                print(f"    ⚠️ Failed to extract text from {resume_path.name}")
                continue
            
            score_result = self.basic_filter.score_resume_comprehensive(
                resume_text, 
                resume_path,
                self.job_ticket
            )
            
            scored_resumes.append(score_result)
        
        scored_resumes.sort(key=lambda x: x["final_score"], reverse=True)
        top_10 = scored_resumes[:10]
        
        print("\n📊 Top 10 Candidates (Based on Latest Requirements):")
        for i, candidate in enumerate(top_10):
            print(f"  {i+1}. {candidate['filename']} - Score: {candidate['final_score']:.2%}")
            print(f"      Skills: {len(candidate['matched_skills'])}/{len(self.job_ticket.tech_stack)} matched")
            print(f"      Experience: {candidate['detected_experience_years']} years")
        
        review_summary = self._prepare_agent_review_data(top_10)
        
        self.user_proxy.initiate_chat(
            self.basic_filter_agent,
            message=f"""Review these top 10 candidates selected using LATEST requirements:

{json.dumps(review_summary, indent=2)}

Confirm they meet the updated requirements, especially the new skills: {', '.join(self.job_ticket.tech_stack)}
""",
            max_turns=1
        )
        
        return {
            "all_resumes": scored_resumes,
            "top_10": top_10,
            "agent_review": self.basic_filter_agent.last_message()["content"],
            "scoring_criteria": {
                "latest_skills_required": self.job_ticket.tech_stack,
                "experience_range": self.job_ticket.experience_required,
                "location": self.job_ticket.location
            }
        }
    
    def _prepare_agent_review_data(self, top_10: List[Dict]) -> List[Dict]:
        """Prepare summary data for agent review"""
        summary = []
        
        for i, candidate in enumerate(top_10):
            summary.append({
                "rank": i + 1,
                "filename": candidate["filename"],
                "overall_score": f"{candidate['final_score']:.1%}",
                "skill_match": f"{candidate['skill_score']:.1%} ({len(candidate['matched_skills'])}/{len(self.job_ticket.tech_stack)})",
                "matched_skills": candidate["matched_skills"],
                "missing_skills": [s for s in self.job_ticket.tech_stack if s not in candidate["matched_skills"]],
                "experience": f"{candidate['detected_experience_years']} years (Score: {candidate['experience_score']:.1%})",
                "location_match": "Yes" if candidate['location_score'] > 0 else "No"
            })
        
        return summary
    
    def _advanced_filtering(self, initial_results: Dict) -> Dict:
        """Stage 2: Advanced filtering with detailed analysis"""
        top_10 = initial_results["top_10"]
        
        detailed_candidates = []
        for i, candidate in enumerate(top_10):
            resume_text = ResumeExtractor.extract_text(Path(candidate["file_path"]))
            
            max_chars = 2000
            if len(resume_text) > max_chars:
                resume_text = resume_text[:max_chars] + "\n[... truncated]"
            
            detailed_candidates.append({
                "rank": i + 1,
                "filename": candidate["filename"],
                "scores": {
                    "overall": f"{candidate['final_score']:.1%}",
                    "skills": f"{candidate['skill_score']:.1%}",
                    "experience": f"{candidate['experience_score']:.1%}"
                },
                "matched_skills": candidate["matched_skills"],
                "missing_skills": [s for s in self.job_ticket.tech_stack if s not in candidate["matched_skills"]],
                "experience_years": candidate["detected_experience_years"],
                "resume_preview": resume_text
            })
        
        analysis_prompt = f"""Analyze these 10 candidates for {self.job_ticket.position}.

LATEST REQUIREMENTS (Must use these):
- Skills: {', '.join(self.job_ticket.tech_stack)}
- Experience: {self.job_ticket.experience_required}
- Location: {self.job_ticket.location}

CANDIDATES:
{json.dumps(detailed_candidates[:5], indent=2)}

[... and 5 more candidates]

Select the TOP 5 based on fit with LATEST requirements.
Format: 
1. [Filename] - Key strengths matching latest requirements
2-5. [Continue ranking]
"""
        
        self.user_proxy.initiate_chat(
            self.advanced_filter_agent,
            message=analysis_prompt,
            max_turns=1
        )
        
        top_5_candidates = []
        for i in range(min(5, len(top_10))):
            candidate = top_10[i].copy()
            candidate["final_rank"] = i + 1
            candidate["selection_reason"] = f"Strong match for updated requirements"
            top_5_candidates.append(candidate)
        
        return {
            "top_5_candidates": top_5_candidates,
            "detailed_analysis": self.advanced_filter_agent.last_message()["content"],
            "selection_criteria": "Based on latest job requirements",
            "requirements_version": self.job_ticket.job_details.get('last_updated', 'Unknown')
        }
    
    def _quality_assurance(self, initial_results: Dict, final_results: Dict) -> Dict:
        """QA review ensuring latest requirements were used"""
        qa_prompt = f"""Review the filtering process for compliance with LATEST requirements:

JOB: {self.job_ticket.position}
LAST UPDATED: {self.job_ticket.job_details.get('last_updated', 'Unknown')}

LATEST REQUIREMENTS:
- Skills: {', '.join(self.job_ticket.tech_stack)}
- Experience: {self.job_ticket.experience_required}
- Location: {self.job_ticket.location}
- Salary: {self.job_ticket.salary_range}

PROCESS SUMMARY:
- {len(initial_results['all_resumes'])} resumes processed
- Top 10 selected with scores from {initial_results['top_10'][0]['final_score']:.1%} to {initial_results['top_10'][-1]['final_score']:.1%}
- Final 5 selected

Verify:
1. Were LATEST skill requirements properly evaluated?
2. Does experience range match current requirements?
3. Any concerns about using outdated criteria?
4. Recommendations for improvement?
"""
        
        self.user_proxy.initiate_chat(
            self.qa_agent,
            message=qa_prompt,
            max_turns=1
        )
        
        return {
            "qa_assessment": self.qa_agent.last_message()["content"],
            "requirements_verified": True,
            "qa_timestamp": datetime.now().isoformat()
        }
    
    def _create_enhanced_summary_report(self, results: Dict):
        """Create detailed summary report with update tracking"""
        report_path = self.output_folder / f"summary_report_{self.job_ticket.ticket_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        with open(report_path, 'w') as f:
            f.write(f"RESUME FILTERING SUMMARY REPORT\n")
            f.write(f"{'='*70}\n\n")
            f.write(f"Job Ticket ID: {results['ticket_id']}\n")
            f.write(f"Position: {results['position']}\n")
            f.write(f"Report Generated: {results['timestamp']}\n")
            f.write(f"Job Status: {results['job_status']}\n")
            f.write(f"Requirements Last Updated: {results['requirements_last_updated']}\n")
            
            f.write(f"\n{'='*70}\n")
            f.write(f"LATEST JOB REQUIREMENTS USED:\n")
            f.write(f"{'='*70}\n")
            f.write(f"Experience: {results['latest_requirements']['experience']}\n")
            f.write(f"Skills: {', '.join(results['latest_requirements']['tech_stack'])}\n")
            f.write(f"Location: {results['latest_requirements']['location']}\n")
            f.write(f"Salary: {results['latest_requirements']['salary']}\n")
            f.write(f"Deadline: {results['latest_requirements']['deadline']}\n")
            
            f.write(f"\n{'='*70}\n")
            f.write(f"FILTERING SUMMARY:\n")
            f.write(f"{'='*70}\n")
            f.write(f"Total Resumes Processed: {results['summary']['total_resumes']}\n")
            f.write(f"Stage 1 Selected: {results['summary']['stage1_selected']}\n")
            f.write(f"Final Selected: {results['summary']['final_selected']}\n")
            
            f.write(f"\n{'='*70}\n")
            f.write(f"TOP 5 CANDIDATES (RANKED):\n")
            f.write(f"{'='*70}\n\n")
            
            for i, candidate in enumerate(results['final_top_5']):
                f.write(f"{i+1}. {candidate['filename']}\n")
                f.write(f"   Overall Score: {candidate['final_score']:.1%}\n")
                f.write(f"   Skill Match: {candidate['skill_score']:.1%} ({len(candidate['matched_skills'])}/{len(results['latest_requirements']['tech_stack'])} skills)\n")
                f.write(f"   Matched Skills: {', '.join(candidate['matched_skills'])}\n")
                f.write(f"   Experience: {candidate['detected_experience_years']} years (Score: {candidate['experience_score']:.1%})\n")
                f.write(f"   Location Match: {'Yes' if candidate['location_score'] > 0 else 'No'}\n")
                f.write(f"\n")
            
            f.write(f"\n{'='*70}\n")
            f.write(f"QUALITY ASSURANCE REVIEW:\n")
            f.write(f"{'='*70}\n")
            f.write(results['qa_review']['qa_assessment'])
        
        print(f"\n📄 Summary report created: {report_path}")


def create_job_details_from_applications(folder_path: str):
    """Create a job_details.json file from applications.json"""
    app_file = Path(folder_path) / "applications.json"
    job_file = Path(folder_path) / "job-data.json"  # Changed to match your naming
    
    if not app_file.exists():
        print(f"❌ No applications.json found in {folder_path}")
        return False
    
    # Check if job-description.txt exists
    job_desc_file = Path(folder_path) / "job-description.txt"
    job_description = "We are looking for a talented professional to join our team."
    
    if job_desc_file.exists():
        with open(job_desc_file, 'r', encoding='utf-8') as f:
            job_description = f.read()
            print(f"📝 Found job description in job-description.txt")
    
    try:
        with open(app_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Create job details based on the structure you provided
        job_details = {
            "ticket_id": Path(folder_path).name,
            "sender": "hr@company.com",
            "subject": "Job Opening",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "status": "posted",
            "initial_details": {
                "job_title": "Senior Data Engineer",
                "location": "Bangalore, India",
                "experience_required": "5-8 years",
                "salary_range": "INR 25-35 Lakhs per annum",
                "job_description": job_description,
                "required_skills": "Python, SQL, Apache Spark, AWS/GCP, ETL, Data Warehousing",
                "employment_type": "Full-time",
                "deadline": "Open until filled"
            },
            "updates": [],
            "approval_status": "approved",
            "approved": True
        }
        
        # If data is a list, store it as applications
        if isinstance(data, list):
            job_details['applications'] = data
        
        # Save the job details
        with open(job_file, 'w', encoding='utf-8') as f:
            json.dump(job_details, f, indent=2)
        
        print(f"✅ Created job-data.json with default values")
        print(f"📝 Please edit {job_file} to match your actual job requirements")
        return True
        
    except Exception as e:
        print(f"❌ Error creating job-data.json: {e}")
        return False


def main():
    """Main function with batch processing support"""
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description='Resume Filtering System with Ticket Tracking')
    parser.add_argument('ticket_folder', nargs='?', help='Specific ticket folder to process')
    parser.add_argument('--batch', action='store_true', help='Process all tickets in jobs-data folder')
    parser.add_argument('--force', action='store_true', help='Force reprocess already processed tickets')
    parser.add_argument('--status', action='store_true', help='Show status of all tickets')
    parser.add_argument('--reset', type=str, help='Reset specific ticket ID to allow reprocessing')
    parser.add_argument('--reset-all', action='store_true', help='Reset all tracking data')
    parser.add_argument('--tickets', nargs='+', help='Process specific ticket IDs only')
    
    args = parser.parse_args()
    
    # Handle status command
    if args.status:
        processor = BatchProcessor()
        processor.show_status()
        return
    
    # Handle reset commands
    if args.reset_all:
        processor = BatchProcessor()
        processor.tracker.reset_all()
        print("✅ All tracking data has been reset")
        return
    
    if args.reset:
        processor = BatchProcessor()
        if processor.tracker.reset_ticket(args.reset):
            print(f"✅ Ticket {args.reset} has been reset")
        else:
            print(f"❌ Ticket {args.reset} not found in tracking data")
        return
    
    # Handle batch processing
    if args.batch:
        print("🚀 Starting batch processing of all tickets...")
        processor = BatchProcessor()
        processor.process_all_tickets(
            force_reprocess=args.force,
            specific_tickets=args.tickets
        )
        return
    
    # Handle single ticket processing
    if args.ticket_folder:
        ticket_folder = args.ticket_folder
    else:
        # If no arguments, show help
        parser.print_help()
        print("\n📋 Examples:")
        print("  Process single ticket:     python script.py jobs-data/ticket123")
        print("  Process all tickets:       python script.py --batch")
        print("  Force reprocess all:       python script.py --batch --force")
        print("  Process specific tickets:  python script.py --batch --tickets ticket123 ticket456")
        print("  Show status:              python script.py --status")
        print("  Reset ticket:             python script.py --reset ticket123")
        print("  Reset all:                python script.py --reset-all")
        return
    
    # Process single ticket
    if not os.path.exists(ticket_folder):
        print(f"❌ Error: Folder '{ticket_folder}' not found")
        return
    
    # Check if already processed
    tracker = TicketTracker()
    ticket_path = Path(ticket_folder)
    is_processed, info = tracker.is_ticket_processed(ticket_path)
    
    if is_processed and not args.force:
        print(f"✅ Ticket {ticket_path.name} already processed on: {info}")
        print(f"   Use --force to reprocess")
        return
    
    # Check if we need to create job-data.json
    job_data_path = Path(ticket_folder) / "job-data.json"
    job_details_path = Path(ticket_folder) / "job_details.json"
    applications_path = Path(ticket_folder) / "applications.json"
    
    if not job_data_path.exists() and not job_details_path.exists() and applications_path.exists():
        print("\n📋 No job-data.json or job_details.json found, but applications.json exists.")
        print("Would you like to create a job-data.json file? (y/n): ", end="")
        
        response = input().strip().lower()
        if response == 'y':
            if create_job_details_from_applications(ticket_folder):
                print("\n⚠️  Please edit the job-data.json file with your specific requirements")
                print("Then run this script again.")
                return
    
    try:
        print("🚀 Initializing Resume Filtering System...")
        filter_system = UpdatedResumeFilteringSystem(ticket_folder)
        
        results = filter_system.filter_resumes()
        
        if "error" not in results:
            # Mark as processed
            results_file = filter_system.output_folder / f"final_results_{filter_system.job_ticket.ticket_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            tracker.mark_ticket_processed(ticket_path, str(results_file))
            
            print(f"\n{'='*70}")
            print(f"✅ FILTERING COMPLETE - FINAL SUMMARY")
            print(f"{'='*70}")
            print(f"Total resumes processed: {results['summary']['total_resumes']}")
            print(f"Requirements last updated: {results['requirements_last_updated']}")
            print(f"\nTop 5 candidates (using latest requirements):")
            for i, candidate in enumerate(results['final_top_5']):
                print(f"  {i+1}. {candidate['filename']}")
                print(f"      Score: {candidate['final_score']:.1%}")
                print(f"      Skills: {len(candidate['matched_skills'])}/{len(results['latest_requirements']['tech_stack'])} matched")
                print(f"      Experience: {candidate['detected_experience_years']} years")
            
            print(f"\n📁 Results saved in: {filter_system.output_folder}")
    
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()