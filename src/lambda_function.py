import boto3
import json
from datetime import datetime, timedelta
from decimal import Decimal
import os
from typing import Dict, List, Tuple

# Initialize AWS clients
ce_client = boto3.client('ce')
ses_client = boto3.client('ses')
org_client = boto3.client('organizations')

# Configuration
EMAIL_FROM = os.environ.get('EMAIL_FROM', 'noreply@awscostmonitor.com')
EMAIL_TO = os.environ.get('EMAIL_TO').split(',')
ANOMALY_THRESHOLD_PERCENT = float(os.environ.get('ANOMALY_THRESHOLD_PERCENT', '50'))
ANOMALY_THRESHOLD_DOLLARS = float(os.environ.get('ANOMALY_THRESHOLD_DOLLARS', '50'))
AI_SERVICE_MULTIPLIER = float(os.environ.get('AI_SERVICE_MULTIPLIER', '0.5'))  # Lower threshold for AI services

# High-cost AI services that need special monitoring
AI_SERVICES = [
    'Amazon Comprehend',
    'Amazon Bedrock',
    'Amazon Textract',
    'Amazon Rekognition',
    'Amazon Transcribe',
    'Amazon Translate',
    'Amazon Polly',
    'Amazon SageMaker'
]


def lambda_handler(event, context):
    """Main Lambda handler for cost monitoring"""
    try:
        # Get date ranges
        # Include today's data (even if partial) for more up-to-date reporting
        end_date = datetime.now() + timedelta(days=1)  # Tomorrow to include today's data
        start_date = end_date - timedelta(days=2)  # Two days ago
        comparison_start = start_date - timedelta(days=2)  # Four days ago
        
        # Format dates for Cost Explorer
        end_str = end_date.strftime('%Y-%m-%d')
        start_str = start_date.strftime('%Y-%m-%d')
        comparison_start_str = comparison_start.strftime('%Y-%m-%d')
        
        print(f"Fetching costs from {start_str} to {end_str} (includes today's partial data)")
        print(f"Comparing with costs from {comparison_start_str} to {start_str}")
        
        # Get organization accounts
        accounts = get_organization_accounts()
        
        # Get current and previous period costs
        current_costs = get_costs_by_service_and_account(start_str, end_str, accounts)
        previous_costs = get_costs_by_service_and_account(comparison_start_str, start_str, accounts)
        
        # Calculate deltas and detect anomalies
        cost_analysis = analyze_costs(current_costs, previous_costs)
        
        # Check for immediate alerts
        immediate_alerts = check_for_immediate_alerts(cost_analysis)
        
        # Generate and send email report
        email_subject = generate_email_subject(cost_analysis, immediate_alerts)
        email_body = generate_email_body(cost_analysis, immediate_alerts, start_str, end_str)
        
        send_email(email_subject, email_body)
        
        return {
            'statusCode': 200,
            'body': json.dumps('Cost report sent successfully')
        }
        
    except Exception as e:
        print(f"Error in lambda_handler: {str(e)}")
        # Send error notification
        send_error_email(str(e))
        raise


def get_organization_accounts() -> List[Dict]:
    """Get all accounts in the organization"""
    accounts = []
    paginator = org_client.get_paginator('list_accounts')
    
    for page in paginator.paginate():
        for account in page['Accounts']:
            if account['Status'] == 'ACTIVE':
                accounts.append({
                    'Id': account['Id'],
                    'Name': account['Name'],
                    'Email': account['Email']
                })
    
    return accounts


def get_costs_by_service_and_account(start_date: str, end_date: str, accounts: List[Dict]) -> Dict:
    """Get costs broken down by service and account"""
    costs = {}
    
    # Get costs grouped by service and account
    response = ce_client.get_cost_and_usage(
        TimePeriod={
            'Start': start_date,
            'End': end_date
        },
        Granularity='DAILY',
        Metrics=['UnblendedCost', 'UsageQuantity'],
        GroupBy=[
            {'Type': 'DIMENSION', 'Key': 'SERVICE'},
            {'Type': 'DIMENSION', 'Key': 'LINKED_ACCOUNT'}
        ]
    )
    
    # Process results
    for result in response['ResultsByTime']:
        for group in result['Groups']:
            service = group['Keys'][0]
            account_id = group['Keys'][1]
            cost = float(group['Metrics']['UnblendedCost']['Amount'])
            
            if account_id not in costs:
                costs[account_id] = {}
            
            if service not in costs[account_id]:
                costs[account_id][service] = 0
            
            costs[account_id][service] += cost
    
    return costs


def analyze_costs(current: Dict, previous: Dict) -> Dict:
    """Analyze cost changes and detect anomalies"""
    analysis = {
        'total_current': 0,
        'total_previous': 0,
        'total_delta': 0,
        'total_delta_percent': 0,
        'accounts': {},
        'anomalies': [],
        'ai_service_alerts': []
    }
    
    # Calculate totals and analyze by account
    for account_id in current:
        account_current = sum(current[account_id].values())
        account_previous = sum(previous.get(account_id, {}).values())
        account_delta = account_current - account_previous
        account_delta_percent = calculate_percent_change(account_previous, account_current)
        
        analysis['total_current'] += account_current
        analysis['total_previous'] += account_previous
        
        # Analyze services within account
        services_analysis = {}
        for service, cost in current[account_id].items():
            prev_cost = previous.get(account_id, {}).get(service, 0)
            delta = cost - prev_cost
            delta_percent = calculate_percent_change(prev_cost, cost)
            
            services_analysis[service] = {
                'current': cost,
                'previous': prev_cost,
                'delta': delta,
                'delta_percent': delta_percent
            }
            
            # Check for anomalies
            is_ai_service = service in AI_SERVICES
            threshold_percent = ANOMALY_THRESHOLD_PERCENT * AI_SERVICE_MULTIPLIER if is_ai_service else ANOMALY_THRESHOLD_PERCENT
            threshold_dollars = ANOMALY_THRESHOLD_DOLLARS * AI_SERVICE_MULTIPLIER if is_ai_service else ANOMALY_THRESHOLD_DOLLARS
            
            if delta_percent > threshold_percent and delta > threshold_dollars:
                anomaly = {
                    'account_id': account_id,
                    'service': service,
                    'current_cost': cost,
                    'previous_cost': prev_cost,
                    'delta': delta,
                    'delta_percent': delta_percent,
                    'is_ai_service': is_ai_service
                }
                
                if is_ai_service:
                    analysis['ai_service_alerts'].append(anomaly)
                else:
                    analysis['anomalies'].append(anomaly)
        
        analysis['accounts'][account_id] = {
            'current': account_current,
            'previous': account_previous,
            'delta': account_delta,
            'delta_percent': account_delta_percent,
            'services': services_analysis
        }
    
    analysis['total_delta'] = analysis['total_current'] - analysis['total_previous']
    analysis['total_delta_percent'] = calculate_percent_change(analysis['total_previous'], analysis['total_current'])
    
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
    for alert in analysis['ai_service_alerts']:
        if alert['delta'] > 100:  # More than $100 increase in AI service
            alerts.append({
                'type': 'CRITICAL_AI_COST',
                'message': f"⚠️ CRITICAL: {alert['service']} costs increased by ${alert['delta']:.2f} ({alert['delta_percent']:.1f}%)",
                'details': alert
            })
    
    # Check for any service with extreme percentage increase
    for account_id, account_data in analysis['accounts'].items():
        for service, service_data in account_data['services'].items():
            if service_data['delta_percent'] > 500 and service_data['current'] > 10:  # 500% increase and over $10
                alerts.append({
                    'type': 'EXTREME_INCREASE',
                    'message': f"⚠️ ALERT: {service} increased by {service_data['delta_percent']:.0f}% in account {account_id}",
                    'details': {
                        'account_id': account_id,
                        'service': service,
                        **service_data
                    }
                })
    
    return alerts


def generate_email_subject(analysis: Dict, alerts: List[Dict]) -> str:
    """Generate email subject line"""
    if alerts:
        return f"🚨 AWS Cost Alert - Immediate Action Required - ${analysis['total_current']:.2f}"
    elif analysis['total_delta_percent'] > 20:
        return f"⚠️ AWS Cost Report - Costs Up {analysis['total_delta_percent']:.1f}% - ${analysis['total_current']:.2f}"
    else:
        return f"✅ AWS Cost Report - ${analysis['total_current']:.2f} Daily"


def generate_email_body(analysis: Dict, alerts: List[Dict], start_date: str, end_date: str) -> str:
    """Generate HTML email body"""
    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .header {{ background-color: #232f3e; color: white; padding: 20px; text-align: center; }}
            .alert {{ background-color: #ff5252; color: white; padding: 15px; margin: 10px 0; border-radius: 5px; }}
            .warning {{ background-color: #ff9800; color: white; padding: 15px; margin: 10px 0; border-radius: 5px; }}
            .summary {{ background-color: #f5f5f5; padding: 20px; margin: 20px 0; border-radius: 5px; }}
            .increase {{ color: #d32f2f; font-weight: bold; }}
            .decrease {{ color: #388e3c; font-weight: bold; }}
            table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
            th {{ background-color: #232f3e; color: white; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            .service-name {{ font-weight: bold; }}
            .ai-service {{ background-color: #fff3cd; }}
            .footer {{ margin-top: 30px; padding: 20px; background-color: #f5f5f5; text-align: center; font-size: 12px; }}
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
    delta_class = "increase" if analysis['total_delta'] > 0 else "decrease"
    delta_symbol = "+" if analysis['total_delta'] > 0 else ""
    
    # Parse dates for display
    start_display = datetime.strptime(start_date, '%Y-%m-%d').strftime('%b %d, %Y')
    end_display = datetime.strptime(end_date, '%Y-%m-%d').strftime('%b %d, %Y')
    
    html += f"""
        <div class="summary">
            <h2>Cost Summary</h2>
            <p><strong>Report Period:</strong> {start_display} to {end_display}</p>
            <p><strong>Total Cost (48 hours):</strong> ${analysis['total_current']:.2f}</p>
            <p><strong>Previous Period:</strong> ${analysis['total_previous']:.2f}</p>
            <p><strong>Change:</strong> <span class="{delta_class}">{delta_symbol}${analysis['total_delta']:.2f} ({delta_symbol}{analysis['total_delta_percent']:.1f}%)</span></p>
            <p><em>Note: Includes today's partial data if available. AWS Cost Explorer may have up to 24-hour delay.</em></p>
        </div>
    """
    
    # Add detailed breakdown by account
    html += "<h2>Account Breakdown</h2>"
    
    for account_id, account_data in sorted(analysis['accounts'].items(), 
                                          key=lambda x: x[1]['current'], 
                                          reverse=True):
        if account_data['current'] < 0.01:  # Skip accounts with negligible costs
            continue
            
        delta_class = "increase" if account_data['delta'] > 0 else "decrease"
        delta_symbol = "+" if account_data['delta'] > 0 else ""
        
        html += f"""
        <h3>Account: {account_id}</h3>
        <p>Total: ${account_data['current']:.2f} 
           <span class="{delta_class}">({delta_symbol}{account_data['delta_percent']:.1f}%)</span>
        </p>
        """
        
        # Add service breakdown for this account
        if account_data['services']:
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
            
            for service, service_data in sorted(account_data['services'].items(), 
                                               key=lambda x: x[1]['current'], 
                                               reverse=True):
                if service_data['current'] < 0.01:  # Skip services with negligible costs
                    continue
                    
                delta_class = "increase" if service_data['delta'] > 0 else "decrease"
                delta_symbol = "+" if service_data['delta'] > 0 else ""
                row_class = "ai-service" if service in AI_SERVICES else ""
                
                html += f"""
                <tr class="{row_class}">
                    <td class="service-name">{service}</td>
                    <td>${service_data['current']:.2f}</td>
                    <td>${service_data['previous']:.2f}</td>
                    <td class="{delta_class}">{delta_symbol}${service_data['delta']:.2f}</td>
                    <td class="{delta_class}">{delta_symbol}{service_data['delta_percent']:.1f}%</td>
                </tr>
                """
            
            html += "</table>"
    
    # Add footer
    html += """
        <div class="footer">
            <p>This report is generated automatically every 6 hours.</p>
            <p>Yellow highlighted rows indicate AI services which are monitored with stricter thresholds.</p>
            <p>To modify alert thresholds or frequency, update the Lambda function environment variables.</p>
        </div>
    </body>
    </html>
    """
    
    return html


def send_email(subject: str, body: str):
    """Send email via SES"""
    try:
        response = ses_client.send_email(
            Source=EMAIL_FROM,
            Destination={
                'ToAddresses': EMAIL_TO
            },
            Message={
                'Subject': {
                    'Data': subject,
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Html': {
                        'Data': body,
                        'Charset': 'UTF-8'
                    }
                }
            }
        )
        print(f"Email sent successfully. Message ID: {response['MessageId']}")
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        raise


def send_error_email(error_message: str):
    """Send error notification email"""
    try:
        ses_client.send_email(
            Source=EMAIL_FROM,
            Destination={
                'ToAddresses': EMAIL_TO
            },
            Message={
                'Subject': {
                    'Data': '❌ AWS Cost Monitor - Error Occurred',
                    'Charset': 'UTF-8'
                },
                'Body': {
                    'Text': {
                        'Data': f"An error occurred in the AWS Cost Monitor Lambda function:\n\n{error_message}",
                        'Charset': 'UTF-8'
                    }
                }
            }
        )
    except Exception as e:
        print(f"Failed to send error email: {str(e)}")