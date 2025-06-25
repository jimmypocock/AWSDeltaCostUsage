import os
import sys

# Add the src directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import boto3  # noqa: E402
import pytest  # noqa: E402
from freezegun import freeze_time  # noqa: E402
from moto import mock_aws  # noqa: E402


@pytest.fixture
def setup_aws_environment():
    """Set up a complete AWS environment for integration testing"""
    os.environ["EMAIL_FROM"] = "noreply@awscostmonitor.com"
    os.environ["EMAIL_TO"] = "admin@example.com"
    os.environ["ANOMALY_THRESHOLD_PERCENT"] = "50"
    os.environ["ANOMALY_THRESHOLD_DOLLARS"] = "50"
    os.environ["AI_SERVICE_MULTIPLIER"] = "0.5"
    os.environ["USER_TIMEZONE"] = "US/Central"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"

    yield

    # Cleanup
    for key in [
        "EMAIL_FROM",
        "EMAIL_TO",
        "ANOMALY_THRESHOLD_PERCENT",
        "ANOMALY_THRESHOLD_DOLLARS",
        "AI_SERVICE_MULTIPLIER",
        "USER_TIMEZONE",
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

    @freeze_time("2024-06-11 13:00:00")  # 1 PM UTC = 8 AM CDT
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

        # Define cost data for different periods and granularities
        def get_cost_side_effect(**kwargs):
            start_date = kwargs["TimePeriod"]["Start"]

            if start_date == "2024-06-11":
                # Today's data
                return {
                    "ResultsByTime": [
                        {
                            "Groups": [
                                {
                                    "Keys": ["Amazon EC2", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "12.50"}},
                                },
                                {
                                    "Keys": ["Amazon Bedrock", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "10.00"}},
                                },
                            ],
                        }
                    ]
                }
            elif start_date == "2024-06-10":
                # Yesterday's data
                return {
                    "ResultsByTime": [
                        {
                            "Groups": [
                                {
                                    "Keys": ["Amazon EC2", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "150.00"}},
                                },
                                {
                                    "Keys": ["Amazon S3", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "45.00"}},
                                },
                                {
                                    "Keys": ["Amazon Bedrock", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "10.00"}},
                                },
                                {
                                    "Keys": ["Amazon EC2", "123456789013"],
                                    "Metrics": {"UnblendedCost": {"Amount": "80.00"}},
                                },
                            ]
                        }
                    ]
                }
            elif start_date == "2024-06-01":
                # Month to date
                return {
                    "ResultsByTime": [
                        {
                            "Groups": [
                                {
                                    "Keys": ["Amazon EC2", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "1650.00"}},
                                },
                                {
                                    "Keys": ["Amazon S3", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "495.00"}},
                                },
                                {
                                    "Keys": ["Amazon Bedrock", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "110.00"}},
                                },
                            ]
                        }
                    ]
                }
            else:
                # Previous month
                return {
                    "ResultsByTime": [
                        {
                            "Groups": [
                                {
                                    "Keys": ["Amazon EC2", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "4500.00"}},
                                },
                                {
                                    "Keys": ["Amazon S3", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "1350.00"}},
                                },
                            ]
                        }
                    ]
                }

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

            # Verify Cost Explorer was called for all 4 periods
            assert mock_ce_client.get_cost_and_usage.call_count == 4

    @freeze_time("2024-06-11 23:00:00")  # 11 PM UTC = 6 PM CDT
    def test_anomaly_detection_flow(self, setup_aws_environment, lambda_context):
        """Test anomaly detection with AI service alerts"""

        # Set up Organizations
        org_client = boto3.client("organizations", region_name="us-east-1")
        org_client.create_organization(FeatureSet="ALL")
        org_client.create_account(AccountName="Production", Email="prod@example.com")

        ses_client = boto3.client("ses", region_name="us-east-1")
        ses_client.verify_email_identity(EmailAddress="noreply@awscostmonitor.com")
        ses_client.verify_email_identity(EmailAddress="admin@example.com")

        from unittest.mock import MagicMock, patch

        mock_ce_client = MagicMock()

        def get_cost_side_effect(**kwargs):
            start_date = kwargs["TimePeriod"]["Start"]

            if start_date == "2024-06-11":
                # Today's data with AI service spike
                return {
                    "ResultsByTime": [
                        {
                            "Groups": [
                                {
                                    "Keys": ["Amazon Bedrock", "123456789012"],
                                    "Metrics": {
                                        "UnblendedCost": {
                                            "Amount": "900.00"
                                        }  # $900 for today (huge spike)
                                    },
                                },
                            ]
                        }
                    ]
                }
            elif start_date == "2024-06-10":
                # Yesterday's normal data
                return {
                    "ResultsByTime": [
                        {
                            "Groups": [
                                {
                                    "Keys": ["Amazon Bedrock", "123456789012"],
                                    "Metrics": {
                                        "UnblendedCost": {"Amount": "100.00"}
                                    },  # Normal daily cost
                                },
                            ]
                        }
                    ]
                }
            else:
                # Other periods
                return {"ResultsByTime": [{"Groups": []}]}

        mock_ce_client.get_cost_and_usage.side_effect = get_cost_side_effect

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

                # Verify alert email was sent
                subject = mock_send_email.call_args[0][0]
                body = mock_send_email.call_args[0][1]

                # Should have anomaly alert in subject
                assert "Alert" in subject or "Anomalies" in subject

                # Should mention Bedrock in the body
                assert "Amazon Bedrock" in body
                assert "alert" in body.lower() or "anomal" in body.lower()

    def test_timezone_handling(self, setup_aws_environment, lambda_context):
        """Test different timezone configurations"""

        # Test with different timezones
        for timezone in ["US/Eastern", "Europe/London", "Asia/Tokyo"]:
            os.environ["USER_TIMEZONE"] = timezone

            from unittest.mock import MagicMock, patch

            org_client = boto3.client("organizations", region_name="us-east-1")
            org_client.create_organization(FeatureSet="ALL")

            ses_client = boto3.client("ses", region_name="us-east-1")
            ses_client.verify_email_identity(EmailAddress="noreply@awscostmonitor.com")

            mock_ce_client = MagicMock()

            # Create a proper response structure
            def mock_ce_response(**kwargs):
                if kwargs.get("Granularity") == "HOURLY":
                    # Return hourly data with proper structure
                    return {
                        "ResultsByTime": [
                            {
                                "TimePeriod": {
                                    "Start": f"2024-06-11T{i:02d}:00:00Z",
                                    "End": f"2024-06-11T{i+1:02d}:00:00Z",
                                },
                                "Groups": [],
                            }
                            for i in range(8)  # 8 hours of data
                        ]
                    }
                else:
                    # Return daily data
                    return {
                        "ResultsByTime": [
                            {
                                "TimePeriod": {
                                    "Start": kwargs["TimePeriod"]["Start"],
                                    "End": kwargs["TimePeriod"]["End"],
                                },
                                "Groups": [],
                            }
                        ]
                    }

            mock_ce_client.get_cost_and_usage.side_effect = mock_ce_response

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

                    # Verify timezone is mentioned in email
                    body = mock_send_email.call_args[0][1]
                    assert timezone in body

    @freeze_time("2024-06-11 13:00:00")
    def test_four_period_reporting(self, setup_aws_environment, lambda_context):
        """Test that all four time periods are included in the report"""

        org_client = boto3.client("organizations", region_name="us-east-1")
        org_client.create_organization(FeatureSet="ALL")

        ses_client = boto3.client("ses", region_name="us-east-1")
        ses_client.verify_email_identity(EmailAddress="noreply@awscostmonitor.com")

        from unittest.mock import MagicMock, patch

        mock_ce_client = MagicMock()

        # Create a proper response structure
        def mock_ce_response(**kwargs):
            if kwargs.get("Granularity") == "HOURLY":
                # Return hourly data with proper structure
                return {
                    "ResultsByTime": [
                        {
                            "TimePeriod": {
                                "Start": f"2024-06-11T{i:02d}:00:00Z",
                                "End": f"2024-06-11T{i+1:02d}:00:00Z",
                            },
                            "Groups": [
                                {
                                    "Keys": ["Amazon EC2", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "10.00"}},
                                }
                            ],
                        }
                        for i in range(8)  # 8 hours of data
                    ]
                }
            else:
                # Return daily data
                return {
                    "ResultsByTime": [
                        {
                            "TimePeriod": {
                                "Start": kwargs["TimePeriod"]["Start"],
                                "End": kwargs["TimePeriod"]["End"],
                            },
                            "Groups": [
                                {
                                    "Keys": ["Amazon EC2", "123456789012"],
                                    "Metrics": {"UnblendedCost": {"Amount": "100.00"}},
                                }
                            ],
                        }
                    ]
                }

        mock_ce_client.get_cost_and_usage.side_effect = mock_ce_response

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

                # Verify email contains all four periods
                body = mock_send_email.call_args[0][1]
                assert "Today" in body
                assert "Yesterday" in body
                assert "Month to Date" in body
                assert "Full Month" in body

                # Verify Cost Explorer was called 4 times
                assert mock_ce_client.get_cost_and_usage.call_count == 4
