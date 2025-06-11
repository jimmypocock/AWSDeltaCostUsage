# AWS Cost Monitor

Automated AWS cost monitoring solution that sends detailed reports every 6 hours and alerts on anomalies to prevent surprise bills. Perfect for catching runaway costs from expensive services like AWS Comprehend, Bedrock, and other AI services before they impact your budget.

## What This Does

This serverless solution automatically monitors your AWS costs across all accounts in your AWS Organization and:
- 📊 Sends detailed cost reports every 6 hours via email
- 🚨 Alerts immediately when costs spike unexpectedly
- 🎯 Provides extra-sensitive monitoring for expensive AI services
- 📈 Shows cost trends with visual indicators (red for increases, green for decreases)
- 💰 Helps prevent surprise bills by catching issues early

## Features

### 🎯 Smart Anomaly Detection

- **AI Service Monitoring**: Extra-sensitive monitoring for expensive services like Comprehend, Bedrock, Textract
- **Percentage & Dollar Thresholds**: Alerts when costs increase by both percentage AND dollar amount
- **Immediate Alerts**: Critical alerts for runaway costs (like your Lambda/Comprehend incident)

### 📊 Comprehensive Reporting

- **Multi-Account Support**: Monitors all accounts in your AWS Organization
- **Service-Level Breakdown**: See costs by service within each account
- **Delta Analysis**: Shows cost changes from previous period
- **Visual Formatting**: Color-coded HTML emails for easy scanning

### ⏰ Automated Scheduling

- Runs every 6 hours automatically
- Configurable schedule via EventBridge
- Manual trigger support for testing

## Quick Start

### Prerequisites

- AWS CLI configured with appropriate credentials
- SAM CLI installed (`brew install aws-sam-cli`)
- AWS Organizations set up (or single account)
- Access to deploy Lambda functions and create IAM roles
- Python 3.12 or compatible version

### Configuration Setup

1. **Copy the example configuration file:**

   ```bash
   cp .env.example .env
   ```

2. **Edit `.env` and set your email addresses:**

   ```bash
   # Required configuration
   EMAIL_TO=your-email@example.com
   EMAIL_FROM=noreply@your-domain.com

   # Optional: AWS profile
   AWS_PROFILE=your-profile-name

   # Optional: Customize thresholds
   ANOMALY_THRESHOLD_PERCENT=50
   ANOMALY_THRESHOLD_DOLLARS=50
   ```

### Deploy in 2 Minutes

```bash
# Run the deployment script (reads from .env automatically)
./deploy.sh

# Or use command line flags
./deploy.sh --email-to your@email.com --email-from sender@domain.com

# Or use environment variables
export EMAIL_TO=your@email.com
export EMAIL_FROM=sender@domain.com
./deploy.sh

# See all options
./deploy.sh --help
```

### Manual Deployment

```bash
# Build the application
sam build

# Deploy with guided prompts
sam deploy --guided

# Or deploy with parameters from environment
sam deploy \
  --stack-name aws-cost-monitor \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    EmailTo=$EMAIL_TO \
    EmailFrom=$EMAIL_FROM
```

## Post-Deployment Setup

### 1. Verify Email Addresses in SES

**IMPORTANT**: You must verify email addresses before the Lambda can send emails.

1. Go to [SES Verified Identities](https://console.aws.amazon.com/ses/home#/verified-identities)
2. Click "Create identity"
3. Choose "Email address"
4. Enter the email addresses from your `.env` file (both EMAIL_TO and EMAIL_FROM)
5. Check your email and click the verification link
6. Repeat for the sender email if using a custom address

### 2. Test the Function

```bash
# Invoke the function manually
aws lambda invoke \
  --function-name aws-cost-monitor \
  /tmp/test-output.json

# Check the output
cat /tmp/test-output.json

# View logs
aws logs tail /aws/lambda/aws-cost-monitor --follow
```

## Configuration

### Configuration Methods

The deployment script supports three ways to configure settings:

1. **`.env` file** (Recommended)

   - Copy `.env.example` to `.env`
   - Update with your values
   - Automatically loaded by deploy script

2. **Environment Variables**

   ```bash
   export EMAIL_TO=your@email.com
   export EMAIL_FROM=sender@domain.com
   export AWS_PROFILE=your-profile
   ```

3. **Command Line Flags**
   ```bash
   ./deploy.sh --email-to your@email.com --email-from sender@domain.com
   ```

### Available Settings

| Variable                    | Required | Default           | Description                            |
| --------------------------- | -------- | ----------------- | -------------------------------------- |
| `EMAIL_TO`                  | Yes      | -                 | Recipients (comma-separated)           |
| `EMAIL_FROM`                | Yes      | -                 | Sender address (must be verified)      |
| `AWS_PROFILE`               | No       | default           | AWS CLI profile to use                 |
| `STACK_NAME`                | No       | AWSDeltaCostUsage | CloudFormation stack name              |
| `ANOMALY_THRESHOLD_PERCENT` | No       | 50                | Percentage increase to trigger alert   |
| `ANOMALY_THRESHOLD_DOLLARS` | No       | 50                | Dollar increase to trigger alert       |
| `AI_SERVICE_MULTIPLIER`     | No       | 0.5               | Sensitivity multiplier for AI services |

### Adjusting Alert Sensitivity

For your use case with occasional $14 domain purchases but concern about AI service spikes:

- **Normal services**: 50% and $50 thresholds work well
- **AI services**: 25% and $25 thresholds (using 0.5 multiplier)
- **Critical alerts**: Any AI service increase over $100

## Email Report Contents

### Example Email Report

![AWS Cost Monitor Email Example](public/images/example.png)

### Regular Report (Every 6 Hours)

- **Report Period**: Shows last 48 hours of costs (including today's partial data)
- **Total Costs**: Organization-wide spending across all accounts
- **Account Breakdown**: Individual account costs with percentages
- **Service Details**: Drilling down to service-level costs per account
- **Delta Analysis**: Percentage and dollar amount changes from previous period
- **Visual Indicators**: Color-coded for quick scanning (red = increase, green = decrease)
- **AI Service Highlighting**: Special yellow background for expensive AI services

### Alert Scenarios

1. **🚨 Critical AI Cost**: When AI services spike over $100
2. **⚠️ Extreme Increase**: When any service increases by 500%+
3. **⚠️ Cost Warning**: When total costs increase by 20%+

## Monitoring & Troubleshooting

### Check Function Logs

```bash
# Recent logs
aws logs tail /aws/lambda/aws-cost-monitor

# Live tail
aws logs tail /aws/lambda/aws-cost-monitor --follow

# Search for errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/aws-cost-monitor \
  --filter-pattern "ERROR"
```

### Common Issues

1. **No emails received**

   - Check SES verified identities
   - Review Lambda logs for SES errors
   - Ensure Lambda has SES permissions

2. **Cost data missing or outdated**

   - Cost Explorer can have up to 24-hour delay
   - The Lambda now fetches 48 hours of data including today's partial data
   - Ensure Lambda has ce:\* permissions
   - Check organization access
   - Note: AWS Console may show different time periods than the Lambda

3. **Function timeout**
   - Large organizations may need increased timeout
   - Current setting: 5 minutes

## Cost of Running This Solution

Extremely minimal:

- **Lambda**: ~$0.50/month (4 executions/day × 30 days)
- **SES**: $0.10 per 1,000 emails
- **EventBridge**: Free tier covers this usage
- **Total**: Less than $1/month

## Customization

### Adding New AI Services to Monitor

Edit `src/lambda_function.py` and add to the `AI_SERVICES` list:

```python
AI_SERVICES = [
    'Amazon Comprehend',
    'Amazon Bedrock',
    'Your New Service',
    # ...
]
```

### Changing Report Frequency

Update the EventBridge schedule in `template.yaml`:

```yaml
Schedule: rate(6 hours) # Change to: rate(1 hour), cron(0 */4 * * ? *), etc.
```

### Custom Alert Logic

Modify the `check_for_immediate_alerts()` function in `src/lambda_function.py` to add custom alert conditions.

## Removing the Solution

To completely remove all resources:

```bash
# Delete the CloudFormation stack
aws cloudformation delete-stack --stack-name AWSDeltaCostUsage

# Or with profile
aws --profile your-profile cloudformation delete-stack --stack-name AWSDeltaCostUsage

# Remove local files if needed
# rm -rf src/ events/ template.yaml deploy.sh samconfig.toml
```

## Security Notes

- Lambda runs with minimal required permissions
- Email addresses are verified through SES
- No credentials are stored in code
- All data stays within your AWS account
- `.env` files are excluded from git via `.gitignore`
- Never commit sensitive configuration to the repository

## Support

## Monitored Services

The solution monitors all AWS services with special attention to:

### AI Services (Extra Sensitive Monitoring)
- 🤖 Amazon Comprehend
- 🤖 Amazon Bedrock 
- 🤖 Amazon Textract
- 🤖 Amazon Rekognition
- 🤖 Amazon Transcribe
- 🤖 Amazon Translate
- 🤖 Amazon Polly
- 🤖 Amazon SageMaker

### Standard Services
- ⚡ Lambda
- 🗄️ DynamoDB
- 🌐 Amplify
- 📦 S3
- 🚀 CloudFront
- 🌍 Route 53
- And all other AWS services

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.
