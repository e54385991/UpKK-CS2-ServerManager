"""
Email service for sending emails via Gmail API or SMTP
"""
import smtplib
import logging
import json
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from modules import SystemSettings

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails"""
    
    async def send_email(
        self,
        db: AsyncSession,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """
        Send an email using configured email provider
        
        Args:
            db: Database session
            to_email: Recipient email address
            subject: Email subject
            html_content: HTML email body
            text_content: Plain text email body (optional)
        
        Returns:
            True if email sent successfully, False otherwise
        """
        try:
            # Get system settings
            settings = await SystemSettings.get_or_create_settings(db)
            
            if not settings.email_enabled:
                logger.warning("Email sending is disabled in system settings")
                return False
            
            if settings.email_provider == "smtp":
                return await self._send_via_smtp(settings, to_email, subject, html_content, text_content)
            elif settings.email_provider == "gmail":
                return await self._send_via_gmail_api(settings, to_email, subject, html_content, text_content)
            else:
                logger.error(f"Unknown email provider: {settings.email_provider}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending email: {e}", exc_info=True)
            return False
    
    async def _send_via_smtp(
        self,
        settings: SystemSettings,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """Send email via SMTP"""
        try:
            if not settings.smtp_host or not settings.smtp_username or not settings.smtp_password:
                logger.error("SMTP settings not configured")
                return False
            
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = settings.email_from_address or settings.smtp_username
            msg['To'] = to_email
            
            # Add text and HTML parts
            if text_content:
                part1 = MIMEText(text_content, 'plain')
                msg.attach(part1)
            
            part2 = MIMEText(html_content, 'html')
            msg.attach(part2)
            
            # Send email
            if settings.smtp_use_tls:
                server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port)
            
            server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_username, to_email, msg.as_string())
            server.quit()
            
            logger.info(f"Email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending email via SMTP: {e}", exc_info=True)
            return False
    
    async def _send_via_gmail_api(
        self,
        settings: SystemSettings,
        to_email: str,
        subject: str,
        html_content: str,
        text_content: Optional[str] = None
    ) -> bool:
        """Send email via Gmail API with OAuth2"""
        try:
            # Import Gmail API libraries
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.errors import HttpError
            
            # Check if Gmail credentials are configured
            if not settings.gmail_token_json:
                logger.error("Gmail API token not configured. Please complete OAuth2 flow first.")
                return False
            
            # Load credentials from stored token
            try:
                token_data = json.loads(settings.gmail_token_json)
                creds = Credentials(
                    token=token_data.get('token'),
                    refresh_token=token_data.get('refresh_token'),
                    token_uri=token_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
                    client_id=token_data.get('client_id'),
                    client_secret=token_data.get('client_secret'),
                    scopes=token_data.get('scopes', ['https://www.googleapis.com/auth/gmail.send'])
                )
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Invalid Gmail token format: {e}")
                return False
            
            # Build the Gmail service
            service = build('gmail', 'v1', credentials=creds)
            
            # Create message
            message = MIMEMultipart('alternative')
            message['To'] = to_email
            message['From'] = settings.email_from_address or 'noreply@example.com'
            message['Subject'] = subject
            
            # Add text and HTML parts
            if text_content:
                part1 = MIMEText(text_content, 'plain')
                message.attach(part1)
            
            part2 = MIMEText(html_content, 'html')
            message.attach(part2)
            
            # Encode message
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Send message
            send_message = service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute()
            
            logger.info(f"Email sent successfully via Gmail API to {to_email}, Message ID: {send_message['id']}")
            return True
            
        except HttpError as e:
            logger.error(f"Gmail API HTTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending email via Gmail API: {e}", exc_info=True)
            return False
    
    def get_password_reset_template(self, reset_link: str, username: str) -> tuple[str, str]:
        """
        Get password reset email template
        
        Returns:
            Tuple of (html_content, text_content)
        """
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #007bff; color: white; padding: 20px; text-align: center; }}
                .content {{ padding: 20px; background-color: #f9f9f9; }}
                .button {{ display: inline-block; padding: 12px 24px; background-color: #007bff; color: white; text-decoration: none; border-radius: 4px; margin: 20px 0; }}
                .footer {{ text-align: center; padding: 20px; color: #666; font-size: 12px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Password Reset Request</h1>
                </div>
                <div class="content">
                    <p>Hello {username},</p>
                    <p>We received a request to reset your password for your CS2 Server Manager account.</p>
                    <p>Click the button below to reset your password:</p>
                    <p style="text-align: center;">
                        <a href="{reset_link}" class="button">Reset Password</a>
                    </p>
                    <p>Or copy and paste this link into your browser:</p>
                    <p style="word-break: break-all;">{reset_link}</p>
                    <p><strong>This link will expire in 1 hour.</strong></p>
                    <p>If you didn't request a password reset, you can safely ignore this email.</p>
                </div>
                <div class="footer">
                    <p>This is an automated message from CS2 Server Manager.</p>
                    <p>Please do not reply to this email.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""
        Password Reset Request
        
        Hello {username},
        
        We received a request to reset your password for your CS2 Server Manager account.
        
        Click the link below to reset your password:
        {reset_link}
        
        This link will expire in 1 hour.
        
        If you didn't request a password reset, you can safely ignore this email.
        
        ---
        This is an automated message from CS2 Server Manager.
        Please do not reply to this email.
        """
        
        return html_content, text_content


# Global instance
email_service = EmailService()
