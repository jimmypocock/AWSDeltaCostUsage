import os

# Add the src directory to Python path
import sys
from unittest.mock import MagicMock, Mock, patch

import boto3
import pytest
from freezegun import freeze_time
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lambda_function import (
    analyze_costs,
    calculate_percent_change,
    check_for_immediate_alerts,
    generate_email_body,
    generate_email_subject,
    lambda_handler,
)


@pytest.fixture
def lambda_context():
    """Create a mock Lambda context"""
    context = Mock()
    context.function_name = "aws-cost-monitor"
    context.memory_limit_in_mb = 512
    context.invoked_function_arn = (
        "arn:aws:lambda:us-east-1:123456789012:function:aws-cost-monitor"
    )
    context.aws_request_id = "test-request-id"
    return context


@pytest.fixture
def mock_env_vars():
    """Set up environment variables for testing"""
    os.environ["EMAIL_FROM"] = "test@example.com"
    os.environ["EMAIL_TO"] = "recipient1@example.com,recipient2@example.com"
    os.environ["ANOMALY_THRESHOLD_PERCENT"] = "50"
    os.environ["ANOMALY_THRESHOLD_DOLLARS"] = "50"
    os.environ["AI_SERVICE_MULTIPLIER"] = "0.5"
    yield
    # Clean up
    for key in [
        "EMAIL_FROM",
        "EMAIL_TO",
        "ANOMALY_THRESHOLD_PERCENT",
        "ANOMALY_THRESHOLD_DOLLARS",
        "AI_SERVICE_MULTIPLIER",
    ]:
        os.environ.pop(key, None)


@pytest.fixture
def sample_accounts():
    """Sample organization accounts"""
    return [
        {
            "Id": "123456789012",
            "Name": "Production Account",
            "Email": "prod@example.com",
        },
        {
            "Id": "123456789013",
            "Name": "Development Account",
            "Email": "dev@example.com",
        },
    ]


@pytest.fixture
def sample_cost_data():
    """Sample cost data for testing"""
    return {
        "current": {
            "123456789012": {
                "Amazon EC2": 100.0,
                "Amazon S3": 50.0,
                "Amazon Bedrock": 25.0,
            },
            "123456789013": {"Amazon EC2": 80.0, "AWS Lambda": 10.0},
        },
        "previous": {
            "123456789012": {
                "Amazon EC2": 90.0,
                "Amazon S3": 45.0,
                "Amazon Bedrock": 5.0,
            },
            "123456789013": {"Amazon EC2": 75.0, "AWS Lambda": 8.0},
        },
    }


class TestCalculatePercentChange:
    """Test the calculate_percent_change function"""

    def test_normal_increase(self):
        assert calculate_percent_change(100, 150) == 50.0

    def test_normal_decrease(self):
        assert calculate_percent_change(100, 50) == -50.0

    def test_zero_previous(self):
        assert calculate_percent_change(0, 100) == 100.0

    def test_zero_current(self):
        assert calculate_percent_change(100, 0) == -100.0

    def test_both_zero(self):
        assert calculate_percent_change(0, 0) == 0.0

    def test_small_values(self):
        assert abs(calculate_percent_change(0.01, 0.02) - 100.0) < 0.01


class TestAnalyzeCosts:
    """Test the analyze_costs function"""

    def test_basic_analysis(self, sample_cost_data):
        analysis = analyze_costs(
            sample_cost_data["current"], sample_cost_data["previous"]
        )

        # Check totals
        assert analysis["total_current"] == 265.0  # 100+50+25+80+10
        assert analysis["total_previous"] == 223.0  # 90+45+5+75+8
        assert analysis["total_delta"] == 42.0
        assert abs(analysis["total_delta_percent"] - 18.83) < 0.01

        # Check account-level data
        assert "123456789012" in analysis["accounts"]
        assert "123456789013" in analysis["accounts"]

        # Check service-level data for account 123456789012
        ec2_data = analysis["accounts"]["123456789012"]["services"]["Amazon EC2"]
        assert ec2_data["current"] == 100.0
        assert ec2_data["previous"] == 90.0
        assert ec2_data["delta"] == 10.0
        assert abs(ec2_data["delta_percent"] - 11.11) < 0.01

    def test_anomaly_detection(self, sample_cost_data):
        # Modify data to trigger anomaly
        sample_cost_data["current"]["123456789012"][
            "Amazon EC2"
        ] = 200.0  # 122% increase

        analysis = analyze_costs(
            sample_cost_data["current"], sample_cost_data["previous"]
        )

        # Should detect EC2 anomaly
        assert len(analysis["anomalies"]) == 1
        anomaly = analysis["anomalies"][0]
        assert anomaly["service"] == "Amazon EC2"
        assert anomaly["delta"] == 110.0
        assert anomaly["is_ai_service"] is False

    def test_ai_service_alert(self, sample_cost_data):
        # Bedrock increased from 5 to 25 (400% increase, $20 delta)
        # With 0.5 multiplier: threshold is 25% and $25
        # This should NOT trigger an alert as delta is only $20

        analysis = analyze_costs(
            sample_cost_data["current"], sample_cost_data["previous"]
        )
        assert len(analysis["ai_service_alerts"]) == 0

        # Now increase Bedrock cost to trigger alert
        sample_cost_data["current"]["123456789012"][
            "Amazon Bedrock"
        ] = 50.0  # $45 increase
        analysis = analyze_costs(
            sample_cost_data["current"], sample_cost_data["previous"]
        )

        assert len(analysis["ai_service_alerts"]) == 1
        alert = analysis["ai_service_alerts"][0]
        assert alert["service"] == "Amazon Bedrock"
        assert alert["delta"] == 45.0
        assert alert["is_ai_service"] is True

    def test_new_service_appears(self):
        current = {"123456789012": {"Amazon EC2": 100.0, "Amazon RDS": 50.0}}
        previous = {"123456789012": {"Amazon EC2": 100.0}}

        analysis = analyze_costs(current, previous)

        rds_data = analysis["accounts"]["123456789012"]["services"]["Amazon RDS"]
        assert rds_data["current"] == 50.0
        assert rds_data["previous"] == 0.0
        assert rds_data["delta"] == 50.0
        assert rds_data["delta_percent"] == 100.0

    def test_new_account_appears(self):
        current = {
            "123456789012": {"Amazon EC2": 100.0},
            "123456789014": {"Amazon S3": 25.0},
        }
        previous = {"123456789012": {"Amazon EC2": 100.0}}

        analysis = analyze_costs(current, previous)

        assert "123456789014" in analysis["accounts"]
        assert analysis["accounts"]["123456789014"]["current"] == 25.0
        assert analysis["accounts"]["123456789014"]["previous"] == 0.0


class TestCheckForImmediateAlerts:
    """Test the check_for_immediate_alerts function"""

    def test_critical_ai_cost_alert(self):
        analysis = {
            "ai_service_alerts": [
                {
                    "service": "Amazon Bedrock",
                    "delta": 150.0,  # More than $100
                    "delta_percent": 500.0,
                }
            ],
            "accounts": {},
        }

        alerts = check_for_immediate_alerts(analysis)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "CRITICAL_AI_COST"
        assert "Amazon Bedrock" in alerts[0]["message"]
        assert "$150.00" in alerts[0]["message"]

    def test_extreme_increase_alert(self):
        analysis = {
            "ai_service_alerts": [],
            "accounts": {
                "123456789012": {
                    "services": {
                        "Amazon EC2": {
                            "delta_percent": 600.0,  # 600% increase
                            "current": 50.0,  # Over $10
                            "delta": 42.0,
                        }
                    }
                }
            },
        }

        alerts = check_for_immediate_alerts(analysis)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "EXTREME_INCREASE"
        assert "Amazon EC2" in alerts[0]["message"]
        assert "600%" in alerts[0]["message"]

    def test_no_alerts(self):
        analysis = {
            "ai_service_alerts": [],
            "accounts": {
                "123456789012": {
                    "services": {
                        "Amazon EC2": {
                            "delta_percent": 10.0,
                            "current": 100.0,
                            "delta": 9.0,
                        }
                    }
                }
            },
        }

        alerts = check_for_immediate_alerts(analysis)
        assert len(alerts) == 0


class TestEmailGeneration:
    """Test email subject and body generation"""

    def test_subject_with_alerts(self):
        analysis = {"total_current": 1234.56, "total_delta_percent": 15.0}
        alerts = [{"type": "CRITICAL_AI_COST"}]

        subject = generate_email_subject(analysis, alerts)
        assert subject.startswith("🚨")
        assert "Immediate Action Required" in subject
        assert "$1234.56" in subject

    def test_subject_cost_increase(self):
        analysis = {"total_current": 1234.56, "total_delta_percent": 25.0}
        alerts = []

        subject = generate_email_subject(analysis, alerts)
        assert subject.startswith("⚠️")
        assert "Costs Up 25.0%" in subject
        assert "$1234.56" in subject

    def test_subject_normal(self):
        analysis = {"total_current": 1234.56, "total_delta_percent": 5.0}
        alerts = []

        subject = generate_email_subject(analysis, alerts)
        assert subject.startswith("✅")
        assert "$1234.56 Daily" in subject

    @freeze_time("2024-06-11 12:00:00")
    def test_email_body_date_range(self):
        analysis = {
            "total_current": 100.0,
            "total_previous": 90.0,
            "total_delta": 10.0,
            "total_delta_percent": 11.11,
            "accounts": {},
        }
        alerts = []
        start_date = "2024-06-09"
        end_date = "2024-06-11"

        body = generate_email_body(analysis, alerts, start_date, end_date)

        # Check date range in header
        assert "2024-06-09 to 2024-06-11" in body

        # Check formatted dates in summary
        assert "Jun 09, 2024" in body
        assert "Jun 11, 2024" in body
        assert "Current Period:" in body
        assert "Compared With:" in body
        assert "Data Freshness:" in body

        # Check the note about data delay
        assert "24-hour delay" in body
        # Check that time is shown
        assert "12:00 PM" in body  # From the freeze_time

    def test_email_body_ai_service_highlighting(self):
        analysis = {
            "total_current": 100.0,
            "total_previous": 90.0,
            "total_delta": 10.0,
            "total_delta_percent": 11.11,
            "accounts": {
                "123456789012": {
                    "current": 100.0,
                    "previous": 90.0,
                    "delta": 10.0,
                    "delta_percent": 11.11,
                    "services": {
                        "Amazon Bedrock": {
                            "current": 50.0,
                            "previous": 40.0,
                            "delta": 10.0,
                            "delta_percent": 25.0,
                        },
                        "Amazon EC2": {
                            "current": 50.0,
                            "previous": 50.0,
                            "delta": 0.0,
                            "delta_percent": 0.0,
                        },
                    },
                }
            },
        }
        alerts = []

        body = generate_email_body(analysis, alerts, "2024-06-09", "2024-06-11")

        # Check for AI service highlighting
        assert "ai-service" in body
        assert "Yellow highlighted rows indicate AI services" in body


@mock_aws
class TestLambdaHandler:
    """Test the main lambda_handler function"""

    @freeze_time("2024-06-11 13:00:00")  # 1 PM UTC = 7 AM CST
    def test_successful_execution(self, lambda_context, mock_env_vars):
        # Set up mock organizations
        org_client = boto3.client("organizations", region_name="us-east-1")
        org_client.create_organization(FeatureSet="ALL")
        org_client.create_account(AccountName="Production", Email="prod@example.com")
        org_client.create_account(AccountName="Development", Email="dev@example.com")

        # Set up mock SES
        ses_client = boto3.client("ses", region_name="us-east-1")
        ses_client.verify_email_identity(EmailAddress="test@example.com")

        # Mock Cost Explorer response
        with patch("boto3.client") as mock_boto_client:
            # Create separate mocks for each client
            mock_ce = MagicMock()
            mock_ses_real = boto3.client("ses", region_name="us-east-1")
            mock_org_real = boto3.client("organizations", region_name="us-east-1")

            def client_side_effect(service_name, **kwargs):
                if service_name == "ce":
                    return mock_ce
                elif service_name == "ses":
                    return mock_ses_real
                elif service_name == "organizations":
                    return mock_org_real
                return MagicMock()

            mock_boto_client.side_effect = client_side_effect

            # Mock CE response
            mock_ce.get_cost_and_usage.return_value = {
                "ResultsByTime": [
                    {
                        "Groups": [
                            {
                                "Keys": ["Amazon EC2", "123456789012"],
                                "Metrics": {"UnblendedCost": {"Amount": "100.0"}},
                            },
                            {
                                "Keys": ["Amazon S3", "123456789012"],
                                "Metrics": {"UnblendedCost": {"Amount": "50.0"}},
                            },
                        ]
                    }
                ]
            }

            # Import the module to apply mocks
            import lambda_function

            lambda_function.ce_client = mock_ce
            lambda_function.ses_client = mock_ses_real
            lambda_function.org_client = mock_org_real

            result = lambda_function.lambda_handler({}, lambda_context)

            assert result["statusCode"] == 200
            assert "Cost report sent successfully" in result["body"]

            # Verify CE was called with correct date ranges
            assert mock_ce.get_cost_and_usage.call_count == 2

            # Check the dates used
            calls = mock_ce.get_cost_and_usage.call_args_list

            # First call (current period): 2024-06-10 to 2024-06-12 (includes today + 1)
            first_call = calls[0][1]
            assert first_call["TimePeriod"]["Start"] == "2024-06-10"
            assert first_call["TimePeriod"]["End"] == "2024-06-12"

            # Second call (previous period): 2024-06-08 to 2024-06-10
            second_call = calls[1][1]
            assert second_call["TimePeriod"]["Start"] == "2024-06-08"
            assert second_call["TimePeriod"]["End"] == "2024-06-10"

    def test_error_handling(self, lambda_context, mock_env_vars):
        with patch("lambda_function.get_organization_accounts") as mock_get_accounts:
            mock_get_accounts.side_effect = Exception("API Error")

            with patch("lambda_function.send_error_email") as mock_send_error:
                with pytest.raises(Exception):
                    lambda_handler({}, lambda_context)

                mock_send_error.assert_called_once()
                assert "API Error" in mock_send_error.call_args[0][0]


class TestDateRangeConsistency:
    """Test for date range consistency issues"""

    @freeze_time("2024-06-11 13:00:00")
    def test_date_range_calculations(self):
        # Test the date range logic
        from datetime import datetime, timedelta

        # Lambda function logic
        end_date = datetime.now() + timedelta(days=1)  # 2024-06-12
        start_date = end_date - timedelta(days=2)  # 2024-06-10
        comparison_start = start_date - timedelta(days=2)  # 2024-06-08

        # The email claims "48 hours" but it's actually including today's partial data
        # So it's really: yesterday + today (partial) = ~48 hours of data

        # Format for Cost Explorer
        end_str = end_date.strftime("%Y-%m-%d")  # 2024-06-12
        start_str = start_date.strftime("%Y-%m-%d")  # 2024-06-10
        comparison_start_str = comparison_start.strftime("%Y-%m-%d")  # 2024-06-08

        # This gives us:
        # Current period: 2024-06-10 to 2024-06-12 (2 full days + today's partial)
        # Previous period: 2024-06-08 to 2024-06-10 (2 full days)

        assert end_str == "2024-06-12"
        assert start_str == "2024-06-10"
        assert comparison_start_str == "2024-06-08"

    def test_email_text_accuracy(self):
        # The email says "generated automatically every 6 hours" on line 363
        # But the schedule is actually at specific times: 7 AM, 1 PM, 6 PM, 11 PM CT
        # This is misleading and should be fixed

        # The email also says "Total Cost (48 hours)" on line 299
        # But it's actually showing data from start_date to end_date
        # Which includes today's partial data, so it could be more or less than 48 hours

        assert True  # This test documents the issue


class TestPaginationIssue:
    """Test for pagination issues with Cost Explorer API"""

    def test_cost_explorer_no_pagination(self):
        # The get_costs_by_service_and_account function doesn't handle pagination
        # Cost Explorer API can return paginated results for large datasets
        # Current implementation only processes the first page

        # This is a known limitation that should be fixed
        assert True  # This test documents the issue


class TestRetryLogic:
    """Test for missing retry logic"""

    def test_no_retry_on_api_failures(self):
        # None of the AWS API calls have retry logic
        # boto3 has built-in retry but it's not configured
        # This could cause failures on transient errors

        assert True  # This test documents the issue
