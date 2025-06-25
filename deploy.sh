#!/bin/bash

# AWS Cost Monitor Deployment Script

echo "🚀 AWS Cost Monitor Deployment"
echo "=============================="

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    echo "📋 Loading configuration from .env file..."
    set -a
    source .env
    set +a
fi

# Parse command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --profile) AWS_PROFILE="$2"; shift ;;
        --email-to) EMAIL_TO="$2"; shift ;;
        --email-from) EMAIL_FROM="$2"; shift ;;
        --timezone) USER_TIMEZONE="$2"; shift ;;
        --stack-name) STACK_NAME="$2"; shift ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --profile <profile>      AWS profile to use"
            echo "  --email-to <email>       Email address to receive reports"
            echo "  --email-from <email>     Email address to send reports from"
            echo "  --timezone <tz>          Timezone for reports (default: US/Central)"
            echo "  --stack-name <name>      CloudFormation stack name (default: AWSDeltaCostUsage)"
            echo "  -h, --help              Show this help message"
            echo ""
            echo "Configuration can also be set via environment variables or .env file"
            exit 0
            ;;
        *) echo "Unknown parameter: $1"; echo "Use --help for usage information"; exit 1 ;;
    esac
    shift
done

# Set defaults if not provided
STACK_NAME="${STACK_NAME:-AWSDeltaCostUsage}"

# Validate required configuration
if [ -z "$EMAIL_TO" ]; then
    echo "❌ Error: EMAIL_TO is not set!"
    echo ""
    echo "Please set it using one of these methods:"
    echo "  1. Create a .env file (copy .env.example and update values)"
    echo "  2. Export EMAIL_TO environment variable"
    echo "  3. Use --email-to flag"
    exit 1
fi

if [ -z "$EMAIL_FROM" ]; then
    echo "❌ Error: EMAIL_FROM is not set!"
    echo ""
    echo "Please set it using one of these methods:"
    echo "  1. Create a .env file (copy .env.example and update values)"
    echo "  2. Export EMAIL_FROM environment variable"
    echo "  3. Use --email-from flag"
    exit 1
fi

echo "📧 Email To: $EMAIL_TO"
echo "📧 Email From: $EMAIL_FROM"
if [ -n "$USER_TIMEZONE" ]; then
    echo "🕐 Timezone: $USER_TIMEZONE"
fi

# Set AWS CLI commands with profile if provided
if [ -n "$AWS_PROFILE" ]; then
    AWS_CMD="aws --profile $AWS_PROFILE"
    SAM_PROFILE_ARG="--profile $AWS_PROFILE"
    echo "📍 Using AWS Profile: $AWS_PROFILE"
else
    AWS_CMD="aws"
    SAM_PROFILE_ARG=""
fi

# Check if SAM CLI is installed
if ! command -v sam &> /dev/null; then
    echo "❌ SAM CLI is not installed. Please install it first:"
    echo "   brew install aws-sam-cli"
    exit 1
fi

# Check if AWS CLI is configured
if ! $AWS_CMD sts get-caller-identity &> /dev/null; then
    echo "❌ AWS CLI is not configured or profile '$AWS_PROFILE' not found."
    echo ""
    echo "Available profiles:"
    aws configure list-profiles | sed 's/^/   - /'
    echo ""
    echo "Please check your profile name and try again."
    exit 1
fi

# Get AWS account details
ACCOUNT_ID=$($AWS_CMD sts get-caller-identity --query Account --output text)
REGION=$($AWS_CMD configure get region)

echo "📍 Deploying to Account: $ACCOUNT_ID"
echo "📍 Region: $REGION"
echo ""

# Build the SAM application
echo "🔨 Building SAM application..."
sam build

if [ $? -ne 0 ]; then
    echo "❌ Build failed"
    exit 1
fi

# Build parameter overrides
PARAMETER_OVERRIDES="EmailTo=$EMAIL_TO EmailFrom=$EMAIL_FROM"

# Add optional parameters if set
if [ -n "$ANOMALY_THRESHOLD_PERCENT" ]; then
    PARAMETER_OVERRIDES="$PARAMETER_OVERRIDES AnomalyThresholdPercent=$ANOMALY_THRESHOLD_PERCENT"
fi
if [ -n "$ANOMALY_THRESHOLD_DOLLARS" ]; then
    PARAMETER_OVERRIDES="$PARAMETER_OVERRIDES AnomalyThresholdDollars=$ANOMALY_THRESHOLD_DOLLARS"
fi
if [ -n "$AI_SERVICE_MULTIPLIER" ]; then
    PARAMETER_OVERRIDES="$PARAMETER_OVERRIDES AIServiceMultiplier=$AI_SERVICE_MULTIPLIER"
fi
if [ -n "$USER_TIMEZONE" ]; then
    PARAMETER_OVERRIDES="$PARAMETER_OVERRIDES UserTimezone=$USER_TIMEZONE"
fi

# Deploy the SAM application
echo ""
echo "🚀 Deploying SAM application..."
echo "   Stack name: $STACK_NAME"
echo "   Parameters: $PARAMETER_OVERRIDES"
echo ""

sam deploy \
    --stack-name $STACK_NAME \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides $PARAMETER_OVERRIDES \
    --confirm-changeset \
    $SAM_PROFILE_ARG

if [ $? -ne 0 ]; then
    echo "❌ Deployment failed"
    exit 1
fi

echo ""
echo "✅ Deployment successful!"
echo ""
echo "📋 Next Steps:"
echo "1. Verify email addresses in SES:"
echo "   - Go to: https://console.aws.amazon.com/ses/home?region=$REGION#/verified-identities"
echo "   - Add and verify: $EMAIL_TO"
echo "   - Add and verify: $EMAIL_FROM"
echo ""
echo "2. Test the function:"
if [ -n "$AWS_PROFILE" ]; then
    echo "   aws --profile $AWS_PROFILE lambda invoke --function-name aws-cost-monitor /tmp/test-output.json"
else
    echo "   aws lambda invoke --function-name aws-cost-monitor /tmp/test-output.json"
fi
echo "   cat /tmp/test-output.json"
echo ""
echo "3. Check logs:"
if [ -n "$AWS_PROFILE" ]; then
    echo "   aws --profile $AWS_PROFILE logs tail /aws/lambda/aws-cost-monitor --follow"
else
    echo "   aws logs tail /aws/lambda/aws-cost-monitor --follow"
fi
echo ""
echo "4. The function will run automatically every 6 hours"
echo ""
echo "⚠️  Important: Make sure to complete email verification in SES before the function runs!"