import json
import os
from datetime import datetime, timedelta
from typing import Dict, List

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# Configure retry logic - VERY conservative to avoid cost surprises
retry_config = Config(
    retries={
        "max_attempts": 2,  # Only retry once (2 total attempts)
        "mode": "standard",  # Standard backoff, not adaptive
    }
)

# Initialize AWS clients with retry configuration
# Defer client creation to avoid issues in testing
ce_client = None
ses_client = None
org_client = None


def get_clients():
    """Initialize AWS clients if not already initialized"""
    global ce_client, ses_client, org_client
    if ce_client is None:
        ce_client = boto3.client("ce", config=retry_config)
    if ses_client is None:
        ses_client = boto3.client("ses", config=retry_config)
    if org_client is None:
        org_client = boto3.client("organizations", config=retry_config)
    return ce_client, ses_client, org_client


# Configuration
EMAIL_FROM = os.environ.get("EMAIL_FROM", "noreply@awscostmonitor.com")
EMAIL_TO = (
    os.environ.get("EMAIL_TO", "").split(",") if os.environ.get("EMAIL_TO") else []
)
ANOMALY_THRESHOLD_PERCENT = float(os.environ.get("ANOMALY_THRESHOLD_PERCENT", "50"))
ANOMALY_THRESHOLD_DOLLARS = float(os.environ.get("ANOMALY_THRESHOLD_DOLLARS", "50"))
AI_SERVICE_MULTIPLIER = float(
    os.environ.get("AI_SERVICE_MULTIPLIER", "0.5")
)  # Lower threshold for AI services

# High-cost AI services that need special monitoring
AI_SERVICES = [
    "Amazon Comprehend",
    "Amazon Bedrock",
    "Amazon Textract",
    "Amazon Rekognition",
    "Amazon Transcribe",
    "Amazon Translate",
    "Amazon Polly",
    "Amazon SageMaker",
]


def lambda_handler(event, context):
    """Main Lambda handler for cost monitoring

    Note: Lambda has a 5-minute timeout configured in template.yaml which acts as
    an absolute safety limit for any runaway operations.
    """
    # Initialize clients
    get_clients()

    try:
        # Get date ranges
        # Include today's data (even if partial) for more up-to-date reporting
        end_date = datetime.now() + timedelta(
            days=1
        )  # Tomorrow to include today's data
        start_date = end_date - timedelta(days=2)  # Two days ago
        comparison_start = start_date - timedelta(days=2)  # Four days ago

        # Format dates for Cost Explorer
        end_str = end_date.strftime("%Y-%m-%d")
        start_str = start_date.strftime("%Y-%m-%d")
        comparison_start_str = comparison_start.strftime("%Y-%m-%d")

        print(
            f"Fetching costs from {start_str} to {end_str} (includes today's partial data)"
        )
        print(f"Comparing with costs from {comparison_start_str} to {start_str}")

        # Get organization accounts
        accounts = get_organization_accounts()

        # Get current and previous period costs
        current_costs = get_costs_by_service_and_account(start_str, end_str, accounts)
        previous_costs = get_costs_by_service_and_account(
            comparison_start_str, start_str, accounts
        )

        # Calculate deltas and detect anomalies
        cost_analysis = analyze_costs(current_costs, previous_costs)

        # Check for immediate alerts
        immediate_alerts = check_for_immediate_alerts(cost_analysis)

        # Generate and send email report
        email_subject = generate_email_subject(cost_analysis, immediate_alerts)
        email_body = generate_email_body(
            cost_analysis, immediate_alerts, start_str, end_str
        )

        send_email(email_subject, email_body)

        return {"statusCode": 200, "body": json.dumps("Cost report sent successfully")}

    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        # Send error notification
        send_error_email(str(e))
        raise


def get_organization_accounts() -> List[Dict]:
    """Get all accounts in the organization"""
    accounts = []
    # Ensure client is initialized
    if org_client is None:
        get_clients()
    paginator = org_client.get_paginator("list_accounts")

    for page in paginator.paginate():
        for account in page["Accounts"]:
            if account["Status"] == "ACTIVE":
                accounts.append(
                    {
                        "Id": account["Id"],
                        "Name": account["Name"],
                        "Email": account["Email"],
                    }
                )

    return accounts


def get_costs_by_service_and_account(
    start_date: str, end_date: str, accounts: List[Dict]
) -> Dict:
    """Get costs broken down by service and account with pagination support"""
    costs = {}
    next_page_token = None
    page_count = 0
    max_pages = 10  # Safety limit - Cost Explorer shouldn't have more than 10 pages

    # Ensure client is initialized
    if ce_client is None:
        get_clients()

    while page_count < max_pages:
        try:
            # Build request parameters
            params = {
                "TimePeriod": {"Start": start_date, "End": end_date},
                "Granularity": "DAILY",
                "Metrics": ["UnblendedCost", "UsageQuantity"],
                "GroupBy": [
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                    {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
                ],
            }

            # Add pagination token if available
            if next_page_token:
                params["NextPageToken"] = next_page_token

            # Get costs with automatic retry
            response = ce_client.get_cost_and_usage(**params)

            # Process results
            for result in response["ResultsByTime"]:
                for group in result["Groups"]:
                    service = group["Keys"][0]
                    account_id = group["Keys"][1]
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])

                    if account_id not in costs:
                        costs[account_id] = {}

                    if service not in costs[account_id]:
                        costs[account_id][service] = 0

                    costs[account_id][service] += cost

            # Check for more pages
            next_page_token = response.get("NextPageToken")
            page_count += 1

            if not next_page_token:
                break

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            print(f"Error getting costs: {error_code} - {e}")
            # Don't add extra sleep or retry - let boto3 handle it
            # This prevents infinite retry loops
            raise

    if page_count >= max_pages:
        print(f"WARNING: Hit max pages limit ({max_pages}) - some data may be missing")

    return costs


def analyze_costs(current: Dict, previous: Dict) -> Dict:
    """Analyze cost changes and detect anomalies"""
    analysis = {
        "total_current": 0,
        "total_previous": 0,
        "total_delta": 0,
        "total_delta_percent": 0,
        "accounts": {},
        "anomalies": [],
        "ai_service_alerts": [],
    }

    # Calculate totals and analyze by account
    for account_id in current:
        account_current = sum(current[account_id].values())
        account_previous = sum(previous.get(account_id, {}).values())
        account_delta = account_current - account_previous
        account_delta_percent = calculate_percent_change(
            account_previous, account_current
        )

        analysis["total_current"] += account_current
        analysis["total_previous"] += account_previous

        # Analyze services within account
        services_analysis = {}
        for service, cost in current[account_id].items():
            prev_cost = previous.get(account_id, {}).get(service, 0)
            delta = cost - prev_cost
            delta_percent = calculate_percent_change(prev_cost, cost)

            services_analysis[service] = {
                "current": cost,
                "previous": prev_cost,
                "delta": delta,
                "delta_percent": delta_percent,
            }

            # Check for anomalies
            is_ai_service = service in AI_SERVICES
            threshold_percent = (
                ANOMALY_THRESHOLD_PERCENT * AI_SERVICE_MULTIPLIER
                if is_ai_service
                else ANOMALY_THRESHOLD_PERCENT
            )
            threshold_dollars = (
                ANOMALY_THRESHOLD_DOLLARS * AI_SERVICE_MULTIPLIER
                if is_ai_service
                else ANOMALY_THRESHOLD_DOLLARS
            )

            if delta_percent > threshold_percent and delta > threshold_dollars:
                anomaly = {
                    "account_id": account_id,
                    "service": service,
                    "current_cost": cost,
                    "previous_cost": prev_cost,
                    "delta": delta,
                    "delta_percent": delta_percent,
                    "is_ai_service": is_ai_service,
                }

                if is_ai_service:
                    analysis["ai_service_alerts"].append(anomaly)
                else:
                    analysis["anomalies"].append(anomaly)

        analysis["accounts"][account_id] = {
            "current": account_current,
            "previous": account_previous,
            "delta": account_delta,
            "delta_percent": account_delta_percent,
            "services": services_analysis,
        }

    analysis["total_delta"] = analysis["total_current"] - analysis["total_previous"]
    analysis["total_delta_percent"] = calculate_percent_change(
        analysis["total_previous"], analysis["total_current"]
    )

    return analysis


def calculate_percent_change(previous: float, current: float) -> float:
    """Calculate percentage change"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100


def check_for_immediate_alerts(analysis: Dict) -> List[Dict]:
    """Check for conditions requiring immediate alerts"""
    alerts = []

    # Check AI service alerts (like your Comprehend incident)
    for alert in analysis["ai_service_alerts"]:
        if alert["delta"] > 100:  # More than $100 increase in AI service
            alerts.append(
                {
                    "type": "CRITICAL_AI_COST",
                    "message": (
                        f"⚠️ CRITICAL: {alert['service']} costs increased by "
                        f"${alert['delta']:.2f} ({alert['delta_percent']:.1f}%)"
                    ),
                    "details": alert,
                }
            )

    # Check for any service with extreme percentage increase
    for account_id, account_data in analysis["accounts"].items():
        for service, service_data in account_data["services"].items():
            if (
                service_data["delta_percent"] > 500 and service_data["current"] > 10
            ):  # 500% increase and over $10
                alerts.append(
                    {
                        "type": "EXTREME_INCREASE",
                        "message": (
                            f"⚠️ ALERT: {service} increased by "
                            f"{service_data['delta_percent']:.0f}% in account {account_id}"
                        ),
                        "details": {
                            "account_id": account_id,
                            "service": service,
                            **service_data,
                        },
                    }
                )

    return alerts


def generate_email_subject(analysis: Dict, alerts: List[Dict]) -> str:
    """Generate email subject line"""
    if alerts:
        return (
            f"🚨 AWS Cost Alert - Immediate Action Required - "
            f"${analysis['total_current']:.2f}"
        )
    elif analysis["total_delta_percent"] > 20:
        return (
            f"⚠️ AWS Cost Report - Costs Up {analysis['total_delta_percent']:.1f}% - "
            f"${analysis['total_current']:.2f}"
        )
    else:
        return f"✅ AWS Cost Report - ${analysis['total_current']:.2f} Daily"


def generate_email_body(
    analysis: Dict, alerts: List[Dict], start_date: str, end_date: str
) -> str:
    """Generate HTML email body"""
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .header {{ background-color: #232f3e; color: white; padding: 20px;
                     text-align: center; }}
            .alert {{ background-color: #ff5252; color: white; padding: 15px;
                     margin: 10px 0; border-radius: 5px; }}
            .warning {{ background-color: #ff9800; color: white; padding: 15px;
                       margin: 10px 0; border-radius: 5px; }}
            .summary {{ background-color: #f5f5f5; padding: 20px; margin: 20px 0;
                      border-radius: 5px; }}
            .increase {{ color: #d32f2f; font-weight: bold; }}
            .decrease {{ color: #388e3c; font-weight: bold; }}
            table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            th {{ background-color: #232f3e; color: white; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .service-name {{ font-weight: bold; }}
            .ai-service {{ background-color: #fff3cd; }}
            .footer {{ margin-top: 30px; padding: 20px; background-color: #f5f5f5;
                      text-align: center; font-size: 12px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>AWS Cost Report</h1>
            <p>{start_date} to {end_date}</p>
        </div>
    """

    # Add immediate alerts if any
    if alerts:
        html += "<h2>🚨 Immediate Alerts</h2>"
        for alert in alerts:
            html += f'<div class="alert">{alert["message"]}</div>'

    # Add summary
    delta_class = "increase" if analysis["total_delta"] > 0 else "decrease"
    delta_symbol = "+" if analysis["total_delta"] > 0 else ""

    # Parse dates for display
    start_display = datetime.strptime(start_date, "%Y-%m-%d").strftime("%b %d, %Y")
    end_display = datetime.strptime(end_date, "%Y-%m-%d").strftime("%b %d, %Y")

    # Calculate actual hours covered
    current_date = datetime.now()
    period_end = datetime.strptime(end_date, "%Y-%m-%d")

    # Determine how much of today is included
    if period_end.date() > current_date.date():
        today_hours = current_date.hour + (current_date.minute / 60)
        period_description = f"Last 2 full days + {today_hours:.1f} hours of today"
    else:
        period_description = "48-hour period"

    html += f"""
        <div class="summary">
            <h2>Cost Summary</h2>
            <p><strong>Current Period:</strong> {start_display} to {end_display}
               ({period_description})</p>
            <p><strong>Compared With:</strong> Previous 48-hour period</p>
            <p><strong>Data Freshness:</strong> Includes partial data up to
               {current_date.strftime('%I:%M %p')} today</p>
            <p><strong>Total Cost:</strong> ${analysis['total_current']:.2f}</p>
            <p><strong>Previous Period:</strong> ${analysis['total_previous']:.2f}</p>
            <p><strong>Change:</strong>
               <span class="{delta_class}">{delta_symbol}${analysis['total_delta']:.2f}
               ({delta_symbol}{analysis['total_delta_percent']:.1f}%)</span></p>
            <p><em>Note: AWS Cost Explorer may have up to 24-hour delay in reporting
               some costs.</em></p>
        </div>
    """

    # Add detailed breakdown by account
    html += "<h2>Account Breakdown</h2>"

    for account_id, account_data in sorted(
        analysis["accounts"].items(), key=lambda x: x[1]["current"], reverse=True
    ):
        if account_data["current"] < 0.01:  # Skip accounts with negligible costs
            continue

        delta_class = "increase" if account_data["delta"] > 0 else "decrease"
        delta_symbol = "+" if account_data["delta"] > 0 else ""

        html += f"""
        <h3>Account: {account_id}</h3>
        <p>Total: ${account_data['current']:.2f}
           <span class="{delta_class}">({delta_symbol}{account_data['delta_percent']:.1f}%)</span>
        </p>
        """

        # Add service breakdown for this account
        if account_data["services"]:
            html += """
            <table>
                <tr>
                    <th>Service</th>
                    <th>Current Cost</th>
                    <th>Previous Cost</th>
                    <th>Change</th>
                    <th>% Change</th>
                </tr>
            """

            for service, service_data in sorted(
                account_data["services"].items(),
                key=lambda x: x[1]["current"],
                reverse=True,
            ):
                if (
                    service_data["current"] < 0.01
                ):  # Skip services with negligible costs
                    continue

                delta_class = "increase" if service_data["delta"] > 0 else "decrease"
                delta_symbol = "+" if service_data["delta"] > 0 else ""
                row_class = "ai-service" if service in AI_SERVICES else ""

                html += f"""
                <tr class="{row_class}">
                    <td class="service-name">{service}</td>
                    <td>${service_data['current']:.2f}</td>
                    <td>${service_data['previous']:.2f}</td>
                    <td class="{delta_class}">
                        {delta_symbol}${service_data['delta']:.2f}
                    </td>
                    <td class="{delta_class}">
                        {delta_symbol}{service_data['delta_percent']:.1f}%
                    </td>
                </tr>
                """

            html += "</table>"

    # Add footer
    html += """
        <div class="footer">
            <p>This report is generated at 7 AM, 1 PM, 6 PM, and 11 PM Central Time daily.</p>
            <p>Yellow highlighted rows indicate AI services which are monitored with
               stricter thresholds.</p>
            <p>To modify alert thresholds or frequency, update the Lambda function
               environment variables.</p>
        </div>
    </body>
    </html>
    """

    return html


def send_email(subject: str, body: str):
    """Send email via SES with safety checks"""
    # Import safety utilities
    from email_safety import EmailRateLimiter, safe_send_email

    # Ensure client is initialized
    if ses_client is None:
        get_clients()

    # Create rate limiter (in Lambda, this is per-execution, but still helps)
    rate_limiter = EmailRateLimiter(max_emails_per_hour=10)

    # Use safe email sending
    success, message_id, error = safe_send_email(
        ses_client, EMAIL_FROM, EMAIL_TO, subject, body, rate_limiter
    )

    if not success:
        print(f"Failed to send email: {error}")
        # Don't raise exception for email failures - log and continue
        # This prevents infinite retry loops
        return

    print(f"Email sent successfully. Message ID: {message_id}")


def send_error_email(error_message: str):
    """Send error notification email"""
    # Ensure client is initialized
    if ses_client is None:
        get_clients()

    try:
        ses_client.send_email(
            Source=EMAIL_FROM,
            Destination={"ToAddresses": EMAIL_TO},
            Message={
                "Subject": {
                    "Data": "❌ AWS Cost Monitor - Error Occurred",
                    "Charset": "UTF-8",
                },
                "Body": {
                    "Text": {
                        "Data": (
                            f"An error occurred in the AWS Cost Monitor Lambda "
                            f"function:\n\n{error_message}"
                        ),
                        "Charset": "UTF-8",
                    }
                },
            },
        )
    except Exception as e:
        print(f"Failed to send error email: {str(e)}")
