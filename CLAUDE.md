# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Build and Deploy
```bash
# Setup (first time only)
cp .env.example .env
# Edit .env with your email addresses and other settings

# Quick deployment (recommended)
./deploy.sh
./deploy.sh --profile jimmycpocock  # With specific AWS profile

# Deploy with command-line overrides
./deploy.sh --email-to user@example.com --email-from sender@example.com
./deploy.sh --profile prod --stack-name prod-cost-monitor

# Manual deployment
sam build
sam deploy --guided
sam deploy --guided --profile jimmycpocock  # With specific AWS profile

# Deploy with specific parameters
sam deploy --parameter-overrides EmailTo=user@example.com EmailFrom=sender@example.com
```

### Testing
```bash
# Test Lambda function manually
aws lambda invoke --function-name aws-cost-monitor /tmp/test-output.json
aws --profile jimmycpocock lambda invoke --function-name aws-cost-monitor /tmp/test-output.json
cat /tmp/test-output.json

# Test with local event
sam local invoke CostMonitorFunction -e events/test-event.json

# View logs
aws logs tail /aws/lambda/aws-cost-monitor --follow
aws --profile jimmycpocock logs tail /aws/lambda/aws-cost-monitor --follow
```

### Development
```bash
# Local testing (requires AWS credentials)
sam local start-lambda
sam local invoke CostMonitorFunction

# Update function code only (faster than full deploy)
sam deploy --no-execute-changeset

# Delete the stack
aws cloudformation delete-stack --stack-name AWSDeltaCostUsage
```

## Architecture Overview

This AWS Lambda function monitors AWS costs across all accounts in an AWS Organization and sends email alerts for anomalies. It runs at specific times daily (7 AM, 1 PM, 6 PM, 11 PM CT) via EventBridge.

### Key Components:
- **src/lambda_function.py**: Main Lambda handler that:
  - Fetches costs from Cost Explorer API (48-hour window including today's partial data)
  - Analyzes cost deltas and detects anomalies
  - Sends HTML-formatted email reports via SES
  - Special handling for expensive AI services (Comprehend, Bedrock, etc.)
  - Shows report period dates in email for clarity

- **template.yaml**: SAM template defining:
  - Lambda function with 5-minute timeout
  - Python 3.12 runtime
  - EventBridge schedule (7 AM, 1 PM, 6 PM, 11 PM Central Time)
  - IAM permissions for Cost Explorer, Organizations, and SES
  - Environment variables for configuration (no hardcoded emails)

- **deploy.sh**: Automated deployment script that:
  - Loads configuration from .env file
  - Accepts command-line overrides (--email-to, --email-from, --profile)
  - Validates required configuration
  - Shows helpful error messages with available AWS profiles
  - Provides post-deployment instructions with correct commands

### Cost Anomaly Detection Logic:
- Normal services: Alert when BOTH percentage (50%) AND dollar amount ($50) thresholds exceeded
- AI services: More sensitive monitoring with 0.5x multiplier (25% and $25)
- Critical alerts: Immediate notification for $100+ AI service increases

### Email Report Structure:
- HTML-formatted with color coding (red=increase, green=decrease)
- Shows date range being reported (48-hour window)
- Account-level breakdown with service details
- AI services highlighted with yellow background
- Filters out negligible costs (<$0.01)
- Includes note about AWS Cost Explorer potential delays

### Configuration System:
- Reads from .env file (gitignored for security)
- Supports environment variables
- Accepts command-line overrides
- No hardcoded sensitive information
- .env.example provided as template