import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import pytz

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
USER_TIMEZONE = os.environ.get("USER_TIMEZONE", "US/Central")  # Default to Central Time

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


def get_timezone_aware_dates(user_tz_str: str) -> Dict[str, Tuple[str, str]]:
    """Get date ranges for various periods in the user's timezone

    Returns a dictionary with:
    - today_so_far: midnight to current time today
    - yesterday_full: midnight to midnight yesterday
    - month_to_date: first of month to current time
    - previous_month_full: first to last of previous month
    """
    try:
        user_tz = pytz.timezone(user_tz_str)
    except pytz.exceptions.UnknownTimeZoneError:
        print(f"Unknown timezone: {user_tz_str}, falling back to UTC")
        user_tz = pytz.UTC

    # Get current time in user's timezone
    now_user = datetime.now(user_tz)

    # Today so far (midnight to now in user timezone)
    today_start_user = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_user.astimezone(pytz.UTC)
    now_utc = now_user.astimezone(pytz.UTC)

    # Yesterday full day
    yesterday_user = now_user - timedelta(days=1)
    yesterday_start_user = yesterday_user.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    yesterday_end_user = today_start_user
    yesterday_start_utc = yesterday_start_user.astimezone(pytz.UTC)
    yesterday_end_utc = yesterday_end_user.astimezone(pytz.UTC)

    # Month to date
    month_start_user = now_user.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    month_start_utc = month_start_user.astimezone(pytz.UTC)

    # Previous month full
    if now_user.month == 1:
        prev_month_start_user = now_user.replace(
            year=now_user.year - 1,
            month=12,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        prev_month_start_user = now_user.replace(
            month=now_user.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0
        )

    prev_month_end_user = month_start_user
    prev_month_start_utc = prev_month_start_user.astimezone(pytz.UTC)
    prev_month_end_utc = prev_month_end_user.astimezone(pytz.UTC)

    # Cost Explorer needs dates in YYYY-MM-DD format
    # For partial days, we need to fetch the full day and will handle filtering later
    return {
        "today_so_far": (
            today_start_utc.strftime("%Y-%m-%d"),
            (now_utc + timedelta(days=1)).strftime(
                "%Y-%m-%d"
            ),  # Tomorrow to include today
            now_user,
        ),
        "yesterday_full": (
            yesterday_start_utc.strftime("%Y-%m-%d"),
            yesterday_end_utc.strftime("%Y-%m-%d"),
        ),
        "month_to_date": (
            month_start_utc.strftime("%Y-%m-%d"),
            (now_utc + timedelta(days=1)).strftime(
                "%Y-%m-%d"
            ),  # Tomorrow to include today
            now_user,
        ),
        "previous_month_full": (
            prev_month_start_utc.strftime("%Y-%m-%d"),
            prev_month_end_utc.strftime("%Y-%m-%d"),
        ),
    }


def lambda_handler(event, context):
    """Main Lambda handler for cost monitoring"""
    # Initialize clients
    get_clients()

    try:
        # Get date ranges for all periods
        date_ranges = get_timezone_aware_dates(USER_TIMEZONE)

        print(f"Fetching costs for timezone: {USER_TIMEZONE}")
        print(f"Today so far: {date_ranges['today_so_far'][0]} to now")
        print(
            f"Yesterday: {date_ranges['yesterday_full'][0]} to {date_ranges['yesterday_full'][1]}"
        )
        print(f"Month to date: {date_ranges['month_to_date'][0]} to now")
        print(
            f"Previous month: {date_ranges['previous_month_full'][0]} to {date_ranges['previous_month_full'][1]}"
        )

        # Get organization accounts
        accounts = get_organization_accounts()

        # Fetch costs for each period
        costs_data = {
            "today_so_far": get_costs_by_service_and_account(
                date_ranges["today_so_far"][0],
                date_ranges["today_so_far"][1],
                accounts,
                hourly_granularity=True,
                cutoff_time=date_ranges["today_so_far"][2],
            ),
            "yesterday_full": get_costs_by_service_and_account(
                date_ranges["yesterday_full"][0],
                date_ranges["yesterday_full"][1],
                accounts,
            ),
            "month_to_date": get_costs_by_service_and_account(
                date_ranges["month_to_date"][0],
                date_ranges["month_to_date"][1],
                accounts,
                cutoff_time=date_ranges["month_to_date"][2],
            ),
            "previous_month_full": get_costs_by_service_and_account(
                date_ranges["previous_month_full"][0],
                date_ranges["previous_month_full"][1],
                accounts,
            ),
        }

        # Analyze costs and detect anomalies
        cost_analysis = analyze_all_periods(costs_data)

        # Check for immediate alerts
        immediate_alerts = check_for_immediate_alerts(cost_analysis)

        # Generate and send email report
        email_subject = generate_email_subject(cost_analysis, immediate_alerts)
        email_body = generate_email_body(
            cost_analysis, immediate_alerts, date_ranges, USER_TIMEZONE
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
    start_date: str,
    end_date: str,
    accounts: List[Dict],
    hourly_granularity: bool = False,
    cutoff_time: datetime = None,
) -> Dict:
    """Get costs broken down by service and account with pagination support"""
    costs = {}
    next_page_token = None
    page_count = 0
    max_pages = 10  # Safety limit

    # Ensure client is initialized
    if ce_client is None:
        get_clients()

    while page_count < max_pages:
        try:
            # Build request parameters
            params = {
                "TimePeriod": {"Start": start_date, "End": end_date},
                "Granularity": "HOURLY" if hourly_granularity else "DAILY",
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
                # Check if we should include this time period
                if cutoff_time and hourly_granularity:
                    result_time = datetime.strptime(
                        result["TimePeriod"]["Start"], "%Y-%m-%dT%H:%M:%SZ"
                    )
                    result_time = result_time.replace(tzinfo=pytz.UTC)
                    if result_time >= cutoff_time.astimezone(pytz.UTC):
                        continue  # Skip future hours

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
            raise

    if page_count >= max_pages:
        print(f"WARNING: Hit max pages limit ({max_pages}) - some data may be missing")

    return costs


def calculate_percent_change(previous: float, current: float) -> float:
    """Calculate percentage change"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return ((current - previous) / previous) * 100


def analyze_all_periods(costs_data: Dict) -> Dict:
    """Analyze costs across all periods"""
    analysis = {"periods": {}, "anomalies": [], "ai_service_alerts": []}

    # Analyze each period
    for period_name, period_costs in costs_data.items():
        period_total = 0
        period_by_account = {}

        for account_id, services in period_costs.items():
            account_total = sum(services.values())
            period_total += account_total

            # Get service breakdown
            services_breakdown = {}
            for service, cost in services.items():
                if cost >= 0.01:  # Filter out negligible costs
                    services_breakdown[service] = cost

            if account_total >= 0.01:  # Only include accounts with costs
                period_by_account[account_id] = {
                    "total": account_total,
                    "services": services_breakdown,
                }

        analysis["periods"][period_name] = {
            "total": period_total,
            "accounts": period_by_account,
        }

    # Check for anomalies between yesterday and today
    if "yesterday_full" in costs_data and "today_so_far" in costs_data:
        for account_id in costs_data["today_so_far"]:
            today_services = costs_data["today_so_far"].get(account_id, {})
            yesterday_services = costs_data["yesterday_full"].get(account_id, {})

            for service, today_cost in today_services.items():
                yesterday_cost = yesterday_services.get(service, 0)

                # Pro-rate yesterday's cost based on current time of day
                now = datetime.now(pytz.timezone(USER_TIMEZONE))
                hours_passed = now.hour + (now.minute / 60)
                prorated_yesterday = (yesterday_cost / 24) * hours_passed

                if prorated_yesterday > 0:
                    delta_percent = calculate_percent_change(
                        prorated_yesterday, today_cost
                    )
                    delta_amount = today_cost - prorated_yesterday

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

                    if (
                        delta_percent > threshold_percent
                        and delta_amount > threshold_dollars
                    ):
                        anomaly = {
                            "account_id": account_id,
                            "service": service,
                            "today_cost": today_cost,
                            "expected_cost": prorated_yesterday,
                            "delta": delta_amount,
                            "delta_percent": delta_percent,
                            "is_ai_service": is_ai_service,
                        }

                        if is_ai_service:
                            analysis["ai_service_alerts"].append(anomaly)
                        else:
                            analysis["anomalies"].append(anomaly)

    return analysis


def check_for_immediate_alerts(analysis: Dict) -> List[Dict]:
    """Check for conditions requiring immediate alerts"""
    alerts = []

    # Check AI service alerts
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

    # Check for extreme increases in today's costs
    for anomaly in analysis["anomalies"]:
        if anomaly["delta_percent"] > 500 and anomaly["today_cost"] > 10:
            alerts.append(
                {
                    "type": "EXTREME_INCREASE",
                    "message": (
                        f"⚠️ ALERT: {anomaly['service']} increased by "
                        f"{anomaly['delta_percent']:.0f}% in account {anomaly['account_id']}"
                    ),
                    "details": anomaly,
                }
            )

    return alerts


def generate_email_subject(analysis: Dict, alerts: List[Dict]) -> str:
    """Generate email subject line"""
    today_total = analysis["periods"]["today_so_far"]["total"]

    if alerts:
        return (
            f"🚨 AWS Cost Alert - Immediate Action Required - ${today_total:.2f} Today"
        )
    elif analysis.get("anomalies"):
        return f"⚠️ AWS Cost Report - Anomalies Detected - ${today_total:.2f} Today"
    else:
        return f"✅ AWS Cost Report - ${today_total:.2f} Today"


def generate_email_body(
    analysis: Dict, alerts: List[Dict], date_ranges: Dict, timezone: str
) -> str:
    """Generate HTML email body"""
    # Get current time in user's timezone
    user_tz = pytz.timezone(timezone)
    now_user = datetime.now(user_tz)
    time_str = now_user.strftime("%I:%M %p %Z")
    date_str = now_user.strftime("%B %d, %Y")

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
            .period-section {{ background-color: #fff; padding: 15px; margin: 15px 0;
                             border: 1px solid #ddd; border-radius: 5px; }}
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
            .metric-box {{ display: inline-block; padding: 10px 20px; margin: 5px;
                         background: #e3f2fd; border-radius: 5px; }}
            .metric-label {{ font-size: 12px; color: #666; }}
            .metric-value {{ font-size: 20px; font-weight: bold; color: #1976d2; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>AWS Cost Report</h1>
            <p>{date_str} at {time_str}</p>
        </div>
    """

    # Add immediate alerts if any
    if alerts:
        html += "<h2>🚨 Immediate Alerts</h2>"
        for alert in alerts:
            html += f'<div class="alert">{alert["message"]}</div>'

    # Add cost summary with four metrics
    html += """
        <div class="summary">
            <h2>Cost Summary</h2>
            <div style="text-align: center;">
    """

    # Today so far
    today_total = analysis["periods"]["today_so_far"]["total"]
    hours_passed = now_user.hour + (now_user.minute / 60)
    html += f"""
                <div class="metric-box">
                    <div class="metric-label">Today ({hours_passed:.1f} hours)</div>
                    <div class="metric-value">${today_total:.2f}</div>
                </div>
    """

    # Yesterday full
    yesterday_total = analysis["periods"]["yesterday_full"]["total"]
    html += f"""
                <div class="metric-box">
                    <div class="metric-label">Yesterday (Full Day)</div>
                    <div class="metric-value">${yesterday_total:.2f}</div>
                </div>
    """

    # Month to date
    mtd_total = analysis["periods"]["month_to_date"]["total"]
    html += f"""
                <div class="metric-box">
                    <div class="metric-label">Month to Date</div>
                    <div class="metric-value">${mtd_total:.2f}</div>
                </div>
    """

    # Previous month
    prev_month_total = analysis["periods"]["previous_month_full"]["total"]
    prev_month_name = (now_user.replace(day=1) - timedelta(days=1)).strftime("%B")
    html += f"""
                <div class="metric-box">
                    <div class="metric-label">{prev_month_name} (Full Month)</div>
                    <div class="metric-value">${prev_month_total:.2f}</div>
                </div>
            </div>
        </div>
    """

    # Add anomalies section if any
    if analysis["anomalies"] or analysis["ai_service_alerts"]:
        html += "<h2>⚠️ Detected Anomalies</h2>"
        html += "<p>Based on today's usage compared to yesterday's average:</p>"

        all_anomalies = analysis["anomalies"] + analysis["ai_service_alerts"]
        for anomaly in sorted(all_anomalies, key=lambda x: x["delta"], reverse=True):
            severity = (
                "alert"
                if anomaly["is_ai_service"] and anomaly["delta"] > 100
                else "warning"
            )
            html += f"""
                <div class="{severity}">
                    <strong>{anomaly['service']}</strong> in account {anomaly['account_id']}<br>
                    Current: ${anomaly['today_cost']:.2f} |
                    Expected: ${anomaly['expected_cost']:.2f} |
                    Increase: ${anomaly['delta']:.2f} ({anomaly['delta_percent']:.1f}%)
                </div>
            """

    # Add detailed breakdown for today
    html += '<div class="period-section">'
    html += f"<h2>Today's Costs by Account (as of {time_str})</h2>"

    for account_id, account_data in sorted(
        analysis["periods"]["today_so_far"]["accounts"].items(),
        key=lambda x: x[1]["total"],
        reverse=True,
    ):
        if account_data["total"] < 0.01:
            continue

        html += f"""
        <h3>Account: {account_id}</h3>
        <p>Total: ${account_data['total']:.2f}</p>
        """

        if account_data["services"]:
            html += """
            <table>
                <tr>
                    <th>Service</th>
                    <th>Cost</th>
                </tr>
            """

            for service, cost in sorted(
                account_data["services"].items(), key=lambda x: x[1], reverse=True
            ):
                row_class = "ai-service" if service in AI_SERVICES else ""
                html += f"""
                <tr class="{row_class}">
                    <td class="service-name">{service}</td>
                    <td>${cost:.2f}</td>
                </tr>
                """

            html += "</table>"

    html += "</div>"

    # Add footer
    html += f"""
        <div class="footer">
            <p>This report shows costs in {timezone} timezone.</p>
            <p>Today's costs cover midnight to {time_str}.</p>
            <p>Yellow highlighted rows indicate AI services with enhanced monitoring.</p>
            <p>Note: AWS Cost Explorer may have up to 24-hour delay in reporting some costs.</p>
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

    # Create rate limiter
    rate_limiter = EmailRateLimiter(max_emails_per_hour=10)

    # Use safe email sending
    success, message_id, error = safe_send_email(
        ses_client, EMAIL_FROM, EMAIL_TO, subject, body, rate_limiter
    )

    if not success:
        print(f"Failed to send email: {error}")
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
