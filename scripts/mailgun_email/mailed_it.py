#!/usr/bin/env python3
"""
Mailgun Bulk Email Sender
A comprehensive script for sending bulk emails using Mailgun API with advanced features.
"""

import os
import re
import csv
import json
import time
import logging
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
from pathlib import Path
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('mailgun_sender.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class EmailRecipient:
    """Class to represent an email recipient with optional personalization"""
    email: str
    name: Optional[str] = None
    variables: Optional[Dict] = None
    
    def __post_init__(self):
        """Validate email format"""
        if not self._is_valid_email(self.email):
            raise ValueError(f"Invalid email format: {self.email}")
    
    @staticmethod
    def _is_valid_email(email: str) -> bool:
        """Validate email format using regex"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

class MailgunConfig:
    """Configuration class for Mailgun settings"""
    
    def __init__(self):
        self.api_key = os.getenv('MAILGUN_API_KEY')
        self.domain = os.getenv('MAILGUN_DOMAIN')
        self.base_url = os.getenv('MAILGUN_BASE_URL', 'https://api.mailgun.net/v3')
        self.from_email = os.getenv('MAILGUN_FROM_EMAIL')
        self.from_name = os.getenv('MAILGUN_FROM_NAME', '')
        
        # Email template settings
        self.template_name = os.getenv('MAILGUN_TEMPLATE_NAME')
        self.template_version = os.getenv('MAILGUN_TEMPLATE_VERSION')
        
        # Batch settings
        self.batch_size = int(os.getenv('MAILGUN_BATCH_SIZE', '1000'))
        self.rate_limit_delay = float(os.getenv('MAILGUN_RATE_LIMIT_DELAY', '0.1'))
        
        # Campaign settings
        self.campaign_id = os.getenv('MAILGUN_CAMPAIGN_ID')
        self.tags = os.getenv('MAILGUN_TAGS', '').split(',') if os.getenv('MAILGUN_TAGS') else []
        
        # Tracking settings
        self.track_clicks = os.getenv('MAILGUN_TRACK_CLICKS', 'true').lower() == 'true'
        self.track_opens = os.getenv('MAILGUN_TRACK_OPENS', 'true').lower() == 'true'
        
        self._validate_config()
    
    def _validate_config(self):
        """Validate required configuration"""
        required_fields = ['api_key', 'domain', 'from_email']
        missing_fields = [field for field in required_fields if not getattr(self, field)]
        
        if missing_fields:
            raise ValueError(f"Missing required configuration: {', '.join(missing_fields)}")

class EmailListParser:
    """Class to parse email addresses from various formats"""
    
    @staticmethod
    def parse_from_string(email_string: str, delimiter: str = ',') -> List[EmailRecipient]:
        """Parse emails from a delimited string"""
        emails = []
        for email in email_string.split(delimiter):
            email = email.strip()
            if email:
                try:
                    emails.append(EmailRecipient(email=email))
                except ValueError as e:
                    logger.warning(f"Skipping invalid email: {e}")
        return emails
    
    @staticmethod
    def parse_from_csv(file_path: str, email_column: str = 'email', 
                      name_column: str = None) -> List[EmailRecipient]:
        """Parse emails from CSV file"""
        emails = []
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    try:
                        email = row.get(email_column, '').strip()
                        name = row.get(name_column, '').strip() if name_column else None
                        
                        # Create variables dict from remaining columns
                        variables = {k: v for k, v in row.items() 
                                   if k not in [email_column, name_column] and v}
                        
                        if email:
                            emails.append(EmailRecipient(
                                email=email,
                                name=name if name else None,
                                variables=variables if variables else None
                            ))
                    except ValueError as e:
                        logger.warning(f"Skipping invalid row: {e}")
        except FileNotFoundError:
            logger.error(f"CSV file not found: {file_path}")
        except Exception as e:
            logger.error(f"Error reading CSV file: {e}")
        
        return emails
    
    @staticmethod
    def parse_from_json(file_path: str) -> List[EmailRecipient]:
        """Parse emails from JSON file"""
        emails = []
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                data = json.load(file)
                
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, str):
                            try:
                                emails.append(EmailRecipient(email=item))
                            except ValueError as e:
                                logger.warning(f"Skipping invalid email: {e}")
                        elif isinstance(item, dict) and 'email' in item:
                            try:
                                emails.append(EmailRecipient(
                                    email=item['email'],
                                    name=item.get('name'),
                                    variables=item.get('variables')
                                ))
                            except ValueError as e:
                                logger.warning(f"Skipping invalid email: {e}")
        except FileNotFoundError:
            logger.error(f"JSON file not found: {file_path}")
        except Exception as e:
            logger.error(f"Error reading JSON file: {e}")
        
        return emails

class MailgunSender:
    """Main class for sending emails via Mailgun"""
    
    def __init__(self, config: MailgunConfig):
        self.config = config
        self.session = requests.Session()
        self.session.auth = ('api', self.config.api_key)
    
    def send_bulk_emails(self, recipients: List[EmailRecipient], 
                        subject: str, html_content: str = None, 
                        text_content: str = None) -> Dict:
        """Send bulk emails using Mailgun's batch sending"""
        
        if not html_content and not text_content:
            raise ValueError("Either HTML or text content must be provided")
        
        results = {
            'total_recipients': len(recipients),
            'batches_sent': 0,
            'successful_sends': 0,
            'failed_sends': 0,
            'errors': []
        }
        
        # Split recipients into batches
        batches = self._create_batches(recipients, self.config.batch_size)
        
        for batch_num, batch in enumerate(batches, 1):
            logger.info(f"Sending batch {batch_num}/{len(batches)} ({len(batch)} recipients)")
            
            try:
                batch_result = self._send_batch(batch, subject, html_content, text_content)
                results['batches_sent'] += 1
                results['successful_sends'] += len(batch)
                
                # Rate limiting
                if batch_num < len(batches):
                    time.sleep(self.config.rate_limit_delay)
                    
            except Exception as e:
                error_msg = f"Batch {batch_num} failed: {str(e)}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
                results['failed_sends'] += len(batch)
        
        return results
    
    def send_template_emails(self, recipients: List[EmailRecipient], 
                           subject: str, global_variables: Dict = None) -> Dict:
        """Send emails using Mailgun templates"""
        
        if not self.config.template_name:
            raise ValueError("Template name not configured")
        
        results = {
            'total_recipients': len(recipients),
            'batches_sent': 0,
            'successful_sends': 0,
            'failed_sends': 0,
            'errors': []
        }
        
        batches = self._create_batches(recipients, self.config.batch_size)
        
        for batch_num, batch in enumerate(batches, 1):
            logger.info(f"Sending template batch {batch_num}/{len(batches)} ({len(batch)} recipients)")
            
            try:
                batch_result = self._send_template_batch(batch, subject, global_variables)
                results['batches_sent'] += 1
                results['successful_sends'] += len(batch)
                
                if batch_num < len(batches):
                    time.sleep(self.config.rate_limit_delay)
                    
            except Exception as e:
                error_msg = f"Template batch {batch_num} failed: {str(e)}"
                logger.error(error_msg)
                results['errors'].append(error_msg)
                results['failed_sends'] += len(batch)
        
        return results
    
    def _create_batches(self, recipients: List[EmailRecipient], 
                       batch_size: int) -> List[List[EmailRecipient]]:
        """Split recipients into batches"""
        batches = []
        for i in range(0, len(recipients), batch_size):
            batches.append(recipients[i:i + batch_size])
        return batches
    
    def _send_batch(self, recipients: List[EmailRecipient], subject: str,
                   html_content: str = None, text_content: str = None) -> Dict:
        """Send a single batch of emails"""
        
        # Prepare recipient variables for personalization
        recipient_vars = {}
        to_emails = []
        
        for recipient in recipients:
            if recipient.name:
                to_emails.append(f"{recipient.name} <{recipient.email}>")
            else:
                to_emails.append(recipient.email)
            
            if recipient.variables:
                recipient_vars[recipient.email] = recipient.variables
        
        # Prepare email data
        data = {
            'from': f"{self.config.from_name} <{self.config.from_email}>" if self.config.from_name else self.config.from_email,
            'to': to_emails,
            'subject': subject,
            'o:tracking-clicks': 'yes' if self.config.track_clicks else 'no',
            'o:tracking-opens': 'yes' if self.config.track_opens else 'no'
        }
        
        if html_content:
            data['html'] = html_content
        if text_content:
            data['text'] = text_content
        if recipient_vars:
            data['recipient-variables'] = json.dumps(recipient_vars)
        if self.config.campaign_id:
            data['o:campaign'] = self.config.campaign_id
        if self.config.tags:
            data['o:tag'] = self.config.tags
        
        # Send the batch
        url = f"{self.config.base_url}/{self.config.domain}/messages"
        response = self.session.post(url, data=data)
        response.raise_for_status()
        
        return response.json()
    
    def _send_template_batch(self, recipients: List[EmailRecipient], 
                           subject: str, global_variables: Dict = None) -> Dict:
        """Send a batch using Mailgun templates"""
        
        recipient_vars = {}
        to_emails = []
        
        for recipient in recipients:
            if recipient.name:
                to_emails.append(f"{recipient.name} <{recipient.email}>")                     
            else:
                to_emails.append(recipient.email)

             
            
            # Merge global and individual variables
            vars_dict = global_variables.copy() if global_variables else {}
            if recipient.variables:
                vars_dict.update(recipient.variables)
            
            if recipient.name:
                vars_dict['name'] = recipient.name

            if vars_dict:                
                recipient_vars[recipient.email] = vars_dict

        
        data = {
            'from': f"{self.config.from_name} <{self.config.from_email}>" if self.config.from_name else self.config.from_email,
            'to': to_emails,
            'subject': subject,
            't:text': 'yes',  # Enable template
            'template': self.config.template_name,
            'o:tracking-clicks': 'yes' if self.config.track_clicks else 'no',
            'o:tracking-opens': 'yes' if self.config.track_opens else 'no'
        }

        # Log recipient variables for debugging
        logger.debug(f"Recipient variables: {recipient_vars}")
        
        if self.config.template_version:
            data['t:version'] = self.config.template_version
        if recipient_vars:
            data['recipient-variables'] = json.dumps(recipient_vars)
        if self.config.campaign_id:
            data['o:campaign'] = self.config.campaign_id
        if self.config.tags:
            data['o:tag'] = self.config.tags
        
        url = f"{self.config.base_url}/{self.config.domain}/messages"
        response = self.session.post(url, data=data)
        response.raise_for_status()
        
        return response.json()

def main():
    """Main function demonstrating usage"""
    try:
        # Initialize configuration
        config = MailgunConfig()
        sender = MailgunSender(config)
        parser = EmailListParser()
        
        # Example: Parse emails from different sources
        recipients = []
        
        # From string (comma-separated)
        email_string = "greg.vannoni@gmail.com"
        #recipients.extend(parser.parse_from_string(email_string))
        
        # From CSV file (if exists)
        csv_file = "email_list.csv"
        if Path(csv_file).exists():
            recipients.extend(parser.parse_from_csv(csv_file, email_column='email', name_column='name'))
        
        # From JSON file (if exists)
        json_file = "email_list.json"
        if Path(json_file).exists():
            recipients.extend(parser.parse_from_json(json_file))
        
        if not recipients:
            logger.warning("No valid recipients found. Please provide email addresses.")
            return
        
        logger.info(f"Found {len(recipients)} valid recipients")
        
        # Example email content
        subject = "🚀 Your Code Can Change Lives - Summer 2025 Opportunity"
        html_content = """
        <html>
        <body>
            <h1>Hello {{name|default('there')}}!</h1>
            <p>Welcome to our newsletter. We're excited to have you on board!</p>
            <p>Your subscription details:</p>
            <ul>
                <li>Email: {{email}}</li>
                <li>Subscription Date: {{subscription_date|default('Today')}}</li>
            </ul>
            <p>Best regards,<br>The Newsletter Team</p>
        </body>
        </html>
        """
        
        text_content = """
        Hello {{name|default('there')}}!
        
        Welcome to our newsletter. We're excited to have you on board!
        
        Your subscription details:
        - Email: {{email}}
        - Subscription Date: {{subscription_date|default('Today')}}
        
        Best regards,
        The Newsletter Team
        """
        
        # Send emails
        if config.template_name:
            # Use template if configured
            global_vars = {'company_name': 'Your Company', 'year': '2025'}
            results = sender.send_template_emails(recipients, subject, global_vars)
        else:
            # Use direct HTML/text content
            results = sender.send_bulk_emails(recipients, subject, html_content, text_content)
        
        # Print results
        logger.info("Email sending completed!")
        logger.info(f"Total recipients: {results['total_recipients']}")
        logger.info(f"Successful sends: {results['successful_sends']}")
        logger.info(f"Failed sends: {results['failed_sends']}")
        
        if results['errors']:
            logger.error("Errors encountered:")
            for error in results['errors']:
                logger.error(f"  - {error}")
    
    except Exception as e:
        logger.error(f"Application error: {e}")
        raise

if __name__ == "__main__":
    main()