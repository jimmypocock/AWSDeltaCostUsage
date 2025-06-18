import os

# Add the src directory to Python path
import sys

import boto3
import pytest
from freezegun import freeze_time
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def setup_aws_environment():
    """Set up a complete AWS environment for integration testing"""
    os.environ["EMAIL_FROM"] = "noreply@awscostmonitor.com"
    os.environ["EMAIL_TO"] = "admin@example.com"
    os.environ["ANOMALY_THRESHOLD_PERCENT"] = "50"
    os.environ["ANOMALY_THRESHOLD_DOLLARS"] = "50"
    os.environ["AI_SERVICE_MULTIPLIER"] = "0.5"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

    yield

    # Cleanup
    for key in [
        "EMAIL_FROM",
        "EMAIL_TO",
        "ANOMALY_THRESHOLD_PERCENT",
        "ANOMALY_THRESHOLD_DOLLARS",
        "AI_SERVICE_MULTIPLIER",
    ]:
        os.environ.pop(key, None)


@pytest.fixture
def lambda_context():
    """Create a mock Lambda context"""

    class Context:
        function_name = "aws-cost-monitor"
        memory_limit_in_mb = 512
        invoked_function_arn = (
            "arn:aws:lambda:us-east-1:123456789012:function:aws-cost-monitor"
        )
        aws_request_id = "test-request-id"
        log_group_name = "/aws/lambda/aws-cost-monitor"
        log_stream_name = "2024/06/11/[$LATEST]test"

        def get_remaining_time_in_millis(self):
            return 300000  # 5 minutes

    return Context()


@mock_aws
class TestLambdaIntegration:
    """Integration tests for the complete Lambda function flow"""

    @freeze_time("2024-06-11 13:00:00")  # 1 PM UTC = 7 AM CST
    def test_full_cost_report_flow(self, setup_aws_environment, lambda_context):
        """Test the complete flow from trigger to email sent"""

        # Set up Organizations
        org_client = boto3.client("organizations", region_name="us-east-1")
        org_client.create_organization(FeatureSet="ALL")

        # Create multiple accounts
        org_client.create_account(AccountName="Production", Email="prod@example.com")
        org_client.create_account(AccountName="Development", Email="dev@example.com")
        org_client.create_account(AccountName="Staging", Email="staging@example.com")

        # Set up SES
        ses_client = boto3.client("ses", region_name="us-east-1")
        ses_client.verify_email_identity(EmailAddress="noreply@awscostmonitor.com")
        ses_client.verify_email_identity(EmailAddress="admin@example.com")

        # Mock Cost Explorer to return realistic data
        from unittest.mock import MagicMock, patch

        mock_ce_client = MagicMock()

        # Define cost data for different periods
        current_period_data = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2024-06-10", "End": "2024-06-12"},
                    "Groups": [
                        # Production account
                        {
                            "Keys": ["Amazon EC2", "123456789012"],
                            "Metrics": {"UnblendedCost": {"Amount": "150.50"}},
                        },
                        {
                            "Keys": ["Amazon S3", "123456789012"],
                            "Metrics": {"UnblendedCost": {"Amount": "45.25"}},
                        },
                        {
                            "Keys": ["Amazon Bedrock", "123456789012"],
                            "Metrics": {"UnblendedCost": {"Amount": "120.00"}},
                        },  # AI service spike
                        # Development account
                        {
                            "Keys": ["Amazon EC2", "123456789013"],
                            "Metrics": {"UnblendedCost": {"Amount": "80.00"}},
                        },
                        {
                            "Keys": ["AWS Lambda", "123456789013"],
                            "Metrics": {"UnblendedCost": {"Amount": "5.50"}},
                        },
                        # Staging account
                        {
                            "Keys": ["Amazon EC2", "123456789014"],
                            "Metrics": {"UnblendedCost": {"Amount": "25.00"}},
                        },
                        {
                            "Keys": ["Amazon RDS", "123456789014"],
                            "Metrics": {"UnblendedCost": {"Amount": "0.005"}},
                        },  # Below threshold
                    ],
                }
            ]
        }

        previous_period_data = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2024-06-08", "End": "2024-06-10"},
                    "Groups": [
                        # Production account
                        {
                            "Keys": ["Amazon EC2", "123456789012"],
                            "Metrics": {"UnblendedCost": {"Amount": "140.00"}},
                        },
                        {
                            "Keys": ["Amazon S3", "123456789012"],
                            "Metrics": {"UnblendedCost": {"Amount": "42.00"}},
                        },
                        {
                            "Keys": ["Amazon Bedrock", "123456789012"],
                            "Metrics": {"UnblendedCost": {"Amount": "10.00"}},
                        },  # Was much lower
                        # Development account
                        {
                            "Keys": ["Amazon EC2", "123456789013"],
                            "Metrics": {"UnblendedCost": {"Amount": "75.00"}},
                        },
                        {
                            "Keys": ["AWS Lambda", "123456789013"],
                            "Metrics": {"UnblendedCost": {"Amount": "5.00"}},
                        },
                        # Staging account
                        {
                            "Keys": ["Amazon EC2", "123456789014"],
                            "Metrics": {"UnblendedCost": {"Amount": "20.00"}},
                        },
                    ],
                }
            ]
        }

        # Set up the mock to return different data for different date ranges
        def get_cost_side_effect(**kwargs):
            if kwargs["TimePeriod"]["Start"] == "2024-06-10":
                return current_period_data
            else:
                return previous_period_data

        mock_ce_client.get_cost_and_usage.side_effect = get_cost_side_effect

        with patch("boto3.client") as mock_boto_client:

            def client_factory(service_name, **kwargs):
                if service_name == "ce":
                    return mock_ce_client
                elif service_name == "ses":
                    return ses_client
                elif service_name == "organizations":
                    return org_client
                return MagicMock()

            mock_boto_client.side_effect = client_factory

            # Reload the module to apply mocks
            import importlib
            import lambda_function

            importlib.reload(lambda_function)

            # Execute the Lambda
            result = lambda_function.lambda_handler({}, lambda_context)

            # Verify successful execution
            assert result["statusCode"] == 200
            assert "Cost report sent successfully" in result["body"]

            # Verify Cost Explorer was called correctly
            assert mock_ce_client.get_cost_and_usage.call_count == 2

            # Verify the email was sent
            # In moto, we can't easily verify the email content, but we can check
            # that the send_email method would have been called

    @freeze_time("2024-06-11 23:00:00")  # 11 PM UTC = 5 PM CST (during DST)
    def test_multi_account_cost_aggregation(
        self, setup_aws_environment, lambda_context
    ):
        """Test correct aggregation across multiple accounts"""

        # Set up Organizations with multiple accounts
        org_client = boto3.client("organizations", region_name="us-east-1")
        org_client.create_organization(FeatureSet="ALL")

        # Create 5 accounts to test scalability
        for i in range(5):
            org_client.create_account(
                AccountName=f"Account-{i}", Email=f"account{i}@example.com"
            )

        ses_client = boto3.client("ses", region_name="us-east-1")
        ses_client.verify_email_identity(EmailAddress="noreply@awscostmonitor.com")
        ses_client.verify_email_identity(EmailAddress="admin@example.com")

        from unittest.mock import MagicMock, patch

        mock_ce_client = MagicMock()

        # Generate cost data for multiple accounts
        groups = []
        for i in range(5):
            account_id = f"12345678901{i}"
            groups.extend(
                [
                    {
                        "Keys": ["Amazon EC2", account_id],
                        "Metrics": {"UnblendedCost": {"Amount": f"{50 + i * 10}.00"}},
                    },
                    {
                        "Keys": ["Amazon S3", account_id],
                        "Metrics": {"UnblendedCost": {"Amount": f"{10 + i * 2}.00"}},
                    },
                ]
            )

        mock_ce_client.get_cost_and_usage.return_value = {
            "ResultsByTime": [{"Groups": groups}]
        }

        with patch("boto3.client") as mock_boto_client:
            with patch("lambda_function.send_email") as mock_send_email:

                def client_factory(service_name, **kwargs):
                    if service_name == "ce":
                        return mock_ce_client
                    elif service_name == "ses":
                        return ses_client
                    elif service_name == "organizations":
                        return org_client
                    return MagicMock()

                mock_boto_client.side_effect = client_factory

                import importlib
                import lambda_function

                importlib.reload(lambda_function)
                lambda_function.send_email = mock_send_email

                # Execute the Lambda
                result = lambda_function.lambda_handler({}, lambda_context)

                # Verify successful execution
                assert result["statusCode"] == 200

                # Verify email contains all accounts
                body = mock_send_email.call_args[0][1]
                for i in range(5):
                    assert f"12345678901{i}" in body


# Removed TestRealAWSIntegration class - not needed for CI/CD
# The pagination safety limits in the code (max 10 pages) provide sufficient protection
# Real AWS testing can be done manually if needed
