"""
Email safety utilities to prevent spam, loops, and excessive costs
"""

import hashlib
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

# Initialize DynamoDB for tracking sent emails (optional)
# Uncomment if you want to use DynamoDB for deduplication
# dynamodb = boto3.resource('dynamodb')
# email_tracking_table = dynamodb.Table(os.environ.get('EMAIL_TRACKING_TABLE', 'cost-monitor-emails'))


class EmailRateLimiter:
    """Simple in-memory rate limiter for Lambda execution"""

    def __init__(self, max_emails_per_hour=10):
        self.max_emails_per_hour = max_emails_per_hour
        self.sent_emails = []

    def can_send_email(self, email_hash):
        """Check if we can send an email based on rate limits"""
        current_time = datetime.now()
        hour_ago = current_time - timedelta(hours=1)

        # Clean old entries
        self.sent_emails = [
            (timestamp, hash_val)
            for timestamp, hash_val in self.sent_emails
            if timestamp > hour_ago
        ]

        # Check rate limit
        if len(self.sent_emails) >= self.max_emails_per_hour:
            print(
                f"Rate limit exceeded: {len(self.sent_emails)} emails in the last hour"
            )
            return False

        # Check for duplicate (same content within 30 minutes)
        thirty_min_ago = current_time - timedelta(minutes=30)
        recent_hashes = [
            hash_val
            for timestamp, hash_val in self.sent_emails
            if timestamp > thirty_min_ago
        ]

        if email_hash in recent_hashes:
            print("Duplicate email detected within 30 minutes")
            return False

        return True

    def record_email_sent(self, email_hash):
        """Record that an email was sent"""
        self.sent_emails.append((datetime.now(), email_hash))


def validate_email_addresses(email_list):
    """Validate email addresses format"""
    import re

    email_pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

    valid_emails = []
    for email in email_list:
        email = email.strip()
        if email and email_pattern.match(email):
            valid_emails.append(email)
        else:
            print(f"Invalid email address filtered out: {email}")

    return valid_emails


def calculate_email_hash(subject, body, recipients):
    """Calculate hash of email content for deduplication"""
    content = f"{subject}{body}{','.join(sorted(recipients))}"
    return hashlib.md5(content.encode()).hexdigest()


def check_ses_sending_quota():
    """Check if we're within SES sending limits"""
    ses_client = boto3.client("ses")

    try:
        response = ses_client.get_send_quota()
        max_24_hour_send = response["Max24HourSend"]
        sent_last_24_hours = response["SentLast24Hours"]

        # Leave 20% buffer
        safe_limit = max_24_hour_send * 0.8

        if sent_last_24_hours >= safe_limit:
            print(
                f"WARNING: Approaching SES quota: {sent_last_24_hours}/{max_24_hour_send}"
            )
            return False

        return True
    except ClientError as e:
        print(f"Error checking SES quota: {e}")
        # If we can't check, assume it's safe to proceed
        return True


def is_bounce_or_complaint_suppressed(email_address):
    """Check if email is on SES suppression list"""
    ses_client = boto3.client("sesv2")

    try:
        response = ses_client.get_suppressed_destination(EmailAddress=email_address)
        if response.get("SuppressedDestination"):
            reason = response["SuppressedDestination"]["Reason"]
            print(f"Email {email_address} is suppressed: {reason}")
            return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            # Not on suppression list, which is good
            return False
        print(f"Error checking suppression list: {e}")

    return False


def safe_send_email(
    ses_client, source, destinations, subject, body_html, rate_limiter=None
):
    """
    Safely send email with multiple safety checks

    Returns: (success: bool, message_id: str or None, error: str or None)
    """
    # Validate email addresses
    valid_recipients = validate_email_addresses(destinations)
    if not valid_recipients:
        return False, None, "No valid recipient email addresses"

    # Filter out suppressed emails
    active_recipients = [
        email
        for email in valid_recipients
        if not is_bounce_or_complaint_suppressed(email)
    ]

    if not active_recipients:
        return False, None, "All recipients are on suppression list"

    # Check SES quota
    if not check_ses_sending_quota():
        return False, None, "SES sending quota exceeded"

    # Check rate limiting (if enabled)
    if rate_limiter:
        email_hash = calculate_email_hash(subject, body_html, active_recipients)
        if not rate_limiter.can_send_email(email_hash):
            return False, None, "Rate limit or duplicate detected"

    # Send email
    try:
        response = ses_client.send_email(
            Source=source,
            Destination={"ToAddresses": active_recipients},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": body_html, "Charset": "UTF-8"}},
            },
        )

        message_id = response["MessageId"]

        # Record the sent email
        if rate_limiter:
            rate_limiter.record_email_sent(email_hash)

        print(
            f"Email sent successfully to {len(active_recipients)} recipients. Message ID: {message_id}"
        )
        return True, message_id, None

    except ClientError as e:
        error_message = f"Failed to send email: {str(e)}"
        print(error_message)
        return False, None, error_message


# Example CloudWatch alarm configuration (add to template.yaml)
CLOUDWATCH_ALARM_CONFIG = """
  EmailErrorAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: cost-monitor-email-errors
      AlarmDescription: Alert when cost monitor fails to send emails
      MetricName: Errors
      Namespace: AWS/Lambda
      Statistic: Sum
      Period: 3600  # 1 hour
      EvaluationPeriods: 1
      Threshold: 3  # More than 3 errors in an hour
      Dimensions:
        - Name: FunctionName
          Value: !Ref CostMonitorFunction
      AlarmActions:
        - !Sub 'arn:aws:sns:${AWS::Region}:${AWS::AccountId}:cost-monitor-alerts'
"""
