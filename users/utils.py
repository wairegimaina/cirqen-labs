# utils.py
import random
import string
import uuid
import logging
import traceback
from datetime import datetime
from core.eat import now_eat
from django.contrib.auth import get_user_model

User = get_user_model()

from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from .models import UserProfile, UserPasswordReset

logger = logging.getLogger(__name__)


class UserManagementUtils:

    @staticmethod
    def generate_username(first_name):
        """Generate unique username from first name + 4 random numbers"""
        base_username = first_name.lower().replace(" ", "").replace("-", "")

        for _ in range(100):
            random_numbers = "".join([str(random.randint(0, 9)) for _ in range(4)])
            username = f"{base_username}{random_numbers}"

            if not User.objects.filter(username=username).exists():
                return username

        # Fallback if all attempts fail
        return f"{base_username}{str(uuid.uuid4())[:4]}"

    @staticmethod
    def generate_temp_password():
        """Generate a temporary password"""
        return "".join(random.choices(string.ascii_letters + string.digits, k=12))

    @staticmethod
    def send_welcome_email(user, temp_password, created_by):
        """Send welcome email to new user"""
        subject = f'Welcome to {getattr(settings, "SITE_NAME", "Biomedical Engineering System")}'

        try:
            profile = user.userprofile
            role_info = f"Role: {profile.get_role_display()}"
            if profile.level:
                role_info += f" ({profile.get_level_display()})"
            if profile.department:
                role_info += f"\ndepartment: {profile.department.name}"
            if profile.workshop:
                role_info += f"\nWorkshop: {profile.workshop.name}"
        except Exception:
            role_info = "Role: User"

        # HTML Email Template
        html_message = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Welcome to {getattr(settings, "SITE_NAME", "Biomedical Engineering System")}</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #f4f4f4;
                    padding: 20px;
                }}
                .container {{
                    background-color: #ffffff;
                    border-radius: 10px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                }}
                .header {{
                    background: linear-gradient(135deg, #16a34a 0%, #15803d 100%);
                    color: white;
                    padding: 30px 20px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .welcome-message {{
                    font-size: 18px;
                    color: #2c3e50;
                    margin-bottom: 25px;
                    text-align: center;
                }}
                .info-section {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #667eea;
                    padding: 20px;
                    margin: 20px 0;
                    border-radius: 0 8px 8px 0;
                }}
                .info-section h3 {{
                    color: #2c3e50;
                    margin-top: 0;
                    font-size: 16px;
                    font-weight: 600;
                }}
                .account-details {{
                    list-style: none;
                    padding: 0;
                    margin: 15px 0;
                }}
                .account-details li {{
                    padding: 8px 0;
                    border-bottom: 1px solid #e9ecef;
                    display: flex;
                    justify-content: space-between;
                }}
                .account-details li:last-child {{
                    border-bottom: none;
                }}
                .label {{
                    font-weight: 600;
                    color: #495057;
                }}
                .value {{
                    color: #6c757d;
                }}
                .password-section {{
                    background-color: #fff3cd;
                    border: 1px solid #ffeaa7;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 25px 0;
                    text-align: center;
                }}
                .temp-password {{
                    font-family: 'Courier New', monospace;
                    font-size: 18px;
                    font-weight: bold;
                    color: #e17055;
                    background-color: #ffffff;
                    padding: 10px 15px;
                    border-radius: 5px;
                    border: 2px solid #fdcb6e;
                    display: inline-block;
                    margin: 10px 0;
                    letter-spacing: 1px;
                }}
                .warning {{
                    background-color: #fee;
                    border: 1px solid #fcc;
                    border-radius: 8px;
                    padding: 15px;
                    margin: 20px 0;
                    color: #c7254e;
                }}
                .login-button {{
                    display: inline-block;
                    background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
                    color: white;
                    padding: 12px 30px;
                    text-decoration: none;
                    border-radius: 25px;
                    font-weight: 600;
                    margin: 20px 0;
                    transition: transform 0.2s;
                }}
                .login-button:hover {{
                    transform: translateY(-2px);
                    color: white;
                    text-decoration: none;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔬 Welcome to the Team!</h1>
                </div>

                <div class="content">
                    <div class="welcome-message">
                        <strong>Dear {user.get_full_name() or user.username},</strong>
                    </div>

                    <p>Welcome to the <strong> Btwelve National hospital Biomedical Engineering Management System</strong>! We're excited to have you on board.</p>

                    <p>Your account has been created by <strong>{created_by.get_full_name() or created_by.username}</strong> and is ready for use.</p>

                    <div class="info-section">
                        <h3>📋 Account Details</h3>
                        <ul class="account-details">
                            <li><span class="label">Username:</span><span class="value">{user.username}</span></li>
                            <li><span class="label">Email:</span><span class="value">{user.email}</span></li>
                        </ul>
                    </div>

                    <div class="password-section">
                        <h3>🔑 Temporary Password</h3>
                        <div class="temp-password">{temp_password}</div>
                        <p><em>Please keep this secure and change it immediately after logging in.</em></p>
                    </div>

                    <div class="warning">
                        ⚠️ <strong>SECURITY IMPORTANT:</strong> Please log in and change your password immediately.
                    </div>


                    <p>If you have any questions or need assistance, please don't hesitate to contact your system administrator.</p>

                    <div class="signature">
                        <p><strong>Best regards,</strong><br>
                        System Administrator<br>
                        Btwelve Technologies </p>
                    </div>
                </div>

                <div class="footer">
                    <p>This is an automated message. Please do not reply to this email.</p>
                    <p>© {datetime.now().year} Btwelve Technologies. All rights reserved.</p>
                </div>
            </div>
        </body>
        </html>
        """

        # Plain text version
        plain_message = f"""
Dear {user.get_full_name() or user.username},

Welcome to the Biomedical Engineering Management System!

Your account has been created by {created_by.get_full_name() or created_by.username}.

ACCOUNT DETAILS:
================
Username: {user.username}
Email: {user.email}


TEMPORARY PASSWORD: {temp_password}

⚠️ IMPORTANT: Please log in and change your password immediately.

Login URL: {getattr(settings, "SITE_URL", "http://localhost:8000")}/login/

Best regards,
System Administrator
Biomedical Engineering Management System

---
This is an automated message. Please do not reply to this email.
        """

        # --- Email config diagnostics ---
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        email_backend = getattr(settings, "EMAIL_BACKEND", None)
        email_host = getattr(settings, "EMAIL_HOST", None)
        email_port = getattr(settings, "EMAIL_PORT", None)
        email_use_tls = getattr(settings, "EMAIL_USE_TLS", None)
        email_use_ssl = getattr(settings, "EMAIL_USE_SSL", None)
        email_host_user = getattr(settings, "EMAIL_HOST_USER", None)

        logger.info(
            "[WELCOME EMAIL] Attempting to send welcome email | "
            "to=%s | from=%s | backend=%s | host=%s | port=%s | "
            "use_tls=%s | use_ssl=%s | host_user=%s",
            user.email,
            from_email,
            email_backend,
            email_host,
            email_port,
            email_use_tls,
            email_use_ssl,
            email_host_user,
        )

        try:
            msg = EmailMultiAlternatives(
                subject,
                plain_message,
                from_email,
                [user.email],
            )
            msg.attach_alternative(html_message, "text/html")
            msg.send(fail_silently=False)
            logger.info(
                "[WELCOME EMAIL] Successfully sent welcome email to %s (user: %s)",
                user.email,
                user.username,
            )
            return True
        except Exception as e:
            logger.error(
                "[WELCOME EMAIL] FAILED to send welcome email | "
                "to=%s | user=%s | error=%s | traceback:\n%s",
                user.email,
                user.username,
                str(e),
                traceback.format_exc(),
            )
            return False

    @staticmethod
    def create_password_reset_token(user, created_by):
        """Create password reset token"""
        return UserPasswordReset.objects.create(user=user, created_by=created_by)

    # for delivering the reset password email

    @staticmethod
    def send_password_reset_email(user, reset_code):
        """Send password reset email with verification code"""
        subject = f'Password Reset Code - {getattr(settings, "SITE_NAME", "Biomedical Engineering System")}'

        # HTML Email Template
        html_message = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Password Reset</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    background-color: #f4f4f4;
                    padding: 20px;
                }}
                .container {{
                    background-color: #ffffff;
                    border-radius: 10px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                }}
                .header {{
                    background: linear-gradient(135deg, #16a34a 0%, #15803d 100%);
                    color: white;
                    padding: 30px 20px;
                    text-align: center;
                }}
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                .content {{
                    padding: 30px;
                }}
                .security-alert {{
                    background-color: #fff3cd;
                    border: 1px solid #ffeaa7;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 25px 0;
                    text-align: center;
                }}
                .reset-code {{
                    font-family: 'Courier New', monospace;
                    font-size: 32px;
                    font-weight: bold;
                    color: #e74c3c;
                    background-color: #ffffff;
                    padding: 20px;
                    border-radius: 10px;
                    border: 3px solid #e74c3c;
                    display: inline-block;
                    margin: 20px 0;
                    letter-spacing: 8px;
                    min-width: 200px;
                }}
                .expiry-info {{
                    background-color: #f8f9fa;
                    border-left: 4px solid #ffc107;
                    padding: 15px;
                    margin: 20px 0;
                    border-radius: 0 8px 8px 0;
                }}
                .warning {{
                    background-color: #fee;
                    border: 1px solid #fcc;
                    border-radius: 8px;
                    padding: 15px;
                    margin: 20px 0;
                    color: #c7254e;
                }}
                .instructions {{
                    background-color: #e8f4fd;
                    border: 1px solid #b3d9ff;
                    border-radius: 8px;
                    padding: 20px;
                    margin: 20px 0;
                }}
                .instructions ol {{
                    margin: 10px 0;
                    padding-left: 20px;
                }}
                .instructions li {{
                    margin: 8px 0;
                    font-weight: 500;
                }}
                .reset-button {{
                    display: inline-block;
                    background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
                    color: white;
                    padding: 12px 30px;
                    text-decoration: none;
                    border-radius: 25px;
                    font-weight: 600;
                    margin: 20px 0;
                    transition: transform 0.2s;
                }}
                .reset-button:hover {{
                    transform: translateY(-2px);
                    color: white;
                    text-decoration: none;
                }}
                .footer {{
                    background-color: #f8f9fa;
                    padding: 20px;
                    text-align: center;
                    color: #6c757d;
                    font-size: 14px;
                    border-top: 1px solid #e9ecef;
                }}
                .time-info {{
                    font-size: 14px;
                    color: #6c757d;
                    margin-top: 10px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔐 Password Reset Request</h1>
                </div>

                <div class="content">
                    <div class="security-alert">
                        <h3>⚠️ Security Alert</h3>
                        <p>We received a request to reset your password. If you didn't make this request, please ignore this email and your password will remain unchanged.</p>
                    </div>

                    <p><strong>Dear {user.get_full_name() or user.username},</strong></p>

                    <p>Your password reset verification code is:</p>

                    <div style="text-align: center;">
                        <div class="reset-code">{reset_code}</div>

                    </div>

                    <div class="expiry-info">
                        <strong>⏱️ Important:</strong> This code will expire in <strong>30 minutes</strong> and can only be used once.
                    </div>

                    <div class="instructions">
                        <h3>📋 How to Reset Your Password:</h3>
                        <ol>
                            <li>Go to the password reset page</li>
                            <li>Enter the 6-digit code above</li>
                            <li>Create your new secure password</li>
                            <li>Log in with your new password</li>
                        </ol>
                    </div>


                    <div class="warning">
                        <strong>🛡️ Security Tips:</strong>
                        <ul style="text-align: left; margin: 10px 0;">
                            <li>Never share this code with anyone</li>
                            <li>Use a strong, unique password</li>
                            <li>Log out of all devices after changing your password</li>
                            <li>If you didn't request this, contact support immediately</li>
                        </ul>
                    </div>

                    <p>If you continue to have problems, please contact your system administrator for assistance.</p>
                </div>

                <div class="footer">
                    <p>This is an automated security message. Please do not reply to this email.</p>
                    <p>© {datetime.now().year} Btwelve Technologies. All rights reserved.</p>
                    <p><small>Request IP: {getattr(settings, 'REQUEST_IP', 'Unknown')} | Time: {now_eat().strftime('%Y-%m-%d %H:%M:%S EAT')}</small></p>
                </div>
            </div>
        </body>
        </html>
        """

        # Plain text version
        plain_message = f"""
SECURITY ALERT: Password Reset Request

Dear {user.get_full_name() or user.username},

We received a request to reset your password. If you didn't make this request, please ignore this email.

Your password reset verification code is: {reset_code}

⏱️ IMPORTANT: This code expires in 30 minutes and can only be used once.

HOW TO RESET YOUR PASSWORD:
1. Go to the password reset page
2. Enter the 6-digit code: {reset_code}
3. Create your new secure password
4. Log in with your new password

Reset URL: {getattr(settings, "SITE_URL", "http://localhost:8000")}/password-reset/

SECURITY TIPS:
- Never share this code with anyone
- Use a strong, unique password
- If you didn't request this, contact support immediately

Generated: {now_eat().strftime('%Y-%m-%d %H:%M:%S EAT')}

Best regards,
System Administrator
Biomedical Engineering Management System

---
This is an automated security message. Please do not reply to this email.
        """

        # --- Email config diagnostics ---
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
        email_backend = getattr(settings, "EMAIL_BACKEND", None)
        email_host = getattr(settings, "EMAIL_HOST", None)
        email_port = getattr(settings, "EMAIL_PORT", None)
        email_use_tls = getattr(settings, "EMAIL_USE_TLS", None)
        email_use_ssl = getattr(settings, "EMAIL_USE_SSL", None)
        email_host_user = getattr(settings, "EMAIL_HOST_USER", None)

        logger.info(
            "[RESET EMAIL] Attempting to send password reset email | "
            "to=%s | from=%s | backend=%s | host=%s | port=%s | "
            "use_tls=%s | use_ssl=%s | host_user=%s",
            user.email,
            from_email,
            email_backend,
            email_host,
            email_port,
            email_use_tls,
            email_use_ssl,
            email_host_user,
        )

        try:
            msg = EmailMultiAlternatives(
                subject,
                plain_message,
                from_email,
                [user.email],
            )
            msg.attach_alternative(html_message, "text/html")
            msg.send(fail_silently=False)
            logger.info(
                "[RESET EMAIL] Successfully sent password reset email to %s (user: %s)",
                user.email,
                user.username,
            )
            return True
        except Exception as e:
            logger.error(
                "[RESET EMAIL] FAILED to send password reset email | "
                "to=%s | user=%s | error=%s | traceback:\n%s",
                user.email,
                user.username,
                str(e),
                traceback.format_exc(),
            )
            return False
