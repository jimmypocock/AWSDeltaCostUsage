import os

# Add the src directory to Python path
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import boto3
import pytest
import pytz
from freezegun import freeze_time
from moto import mock_aws

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from lambda_function import (
    analyze_all_periods,
    calculate_percent_change,
    check_for_immediate_alerts,
    generate_email_body,
    generate_email_subject,
    get_timezone_aware_dates,
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
    os.environ["USER_TIMEZONE"] = "US/Central"
    yield
    # Clean up
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
def sample_cost_data_periods():
    """Sample cost data for all periods"""
    return {
        "today_so_far": {
            "123456789012": {
                "Amazon EC2": 50.0,
                "Amazon S3": 25.0,
                "Amazon Bedrock": 15.0,
            },
            "123456789013": {"Amazon EC2": 40.0, "AWS Lambda": 5.0},
        },
        "yesterday_full": {
            "123456789012": {
                "Amazon EC2": 100.0,
                "Amazon S3": 50.0,
                "Amazon Bedrock": 10.0,
            },
            "123456789013": {"Amazon EC2": 80.0, "AWS Lambda": 10.0},
        },
        "month_to_date": {
            "123456789012": {
                "Amazon EC2": 1100.0,
                "Amazon S3": 550.0,
                "Amazon Bedrock": 125.0,
            },
            "123456789013": {"Amazon EC2": 880.0, "AWS Lambda": 110.0},
        },
        "previous_month_full": {
            "123456789012": {
                "Amazon EC2": 3000.0,
                "Amazon S3": 1500.0,
                "Amazon Bedrock": 300.0,
            },
            "123456789013": {"Amazon EC2": 2400.0, "AWS Lambda": 300.0},
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


class TestGetTimezoneAwareDates:
    """Test timezone-aware date calculations"""

    @freeze_time("2024-06-11 14:30:00")  # 2:30 PM UTC
    def test_central_timezone(self):
        # Central time is UTC-5 (CDT) or UTC-6 (CST)
        # In June, it's CDT (UTC-5), so 2:30 PM UTC = 9:30 AM CDT
        dates = get_timezone_aware_dates("US/Central")
        
        # Today so far should be from midnight CDT to current time
        assert dates["today_so_far"][0] == "2024-06-11"  # Midnight CDT = 5 AM UTC on same day
        assert dates["today_so_far"][1] == "2024-06-12"  # Tomorrow for Cost Explorer
        
        # Yesterday should be full day
        assert dates["yesterday_full"][0] == "2024-06-10"
        assert dates["yesterday_full"][1] == "2024-06-11"
        
        # Month to date
        assert dates["month_to_date"][0] == "2024-06-01"
        assert dates["month_to_date"][1] == "2024-06-12"
        
        # Previous month (May)
        assert dates["previous_month_full"][0] == "2024-05-01"
        assert dates["previous_month_full"][1] == "2024-06-01"

    @freeze_time("2024-01-15 18:00:00")  # 6 PM UTC
    def test_eastern_timezone_january(self):
        # Eastern time in January is EST (UTC-5)
        # 6 PM UTC = 1 PM EST
        dates = get_timezone_aware_dates("US/Eastern")
        
        # Verify we handle year boundaries correctly
        assert dates["previous_month_full"][0] == "2023-12-01"
        assert dates["previous_month_full"][1] == "2024-01-01"

    def test_invalid_timezone_fallback(self):
        # Should fall back to UTC for invalid timezone
        dates = get_timezone_aware_dates("Invalid/Timezone")
        assert dates is not None  # Should not crash


class TestAnalyzeAllPeriods:
    """Test the analyze_all_periods function"""

    @freeze_time("2024-06-11 14:30:00")  # 2:30 PM UTC = 9:30 AM CDT
    def test_basic_analysis(self, sample_cost_data_periods):
        analysis = analyze_all_periods(sample_cost_data_periods)
        
        # Check period totals
        assert analysis["periods"]["today_so_far"]["total"] == 135.0  # 50+25+15+40+5
        assert analysis["periods"]["yesterday_full"]["total"] == 250.0  # 100+50+10+80+10
        assert analysis["periods"]["month_to_date"]["total"] == 2765.0
        assert analysis["periods"]["previous_month_full"]["total"] == 7500.0
        
        # Check account breakdowns exist
        assert "123456789012" in analysis["periods"]["today_so_far"]["accounts"]
        assert "123456789013" in analysis["periods"]["today_so_far"]["accounts"]

    @freeze_time("2024-06-11 14:30:00")  # 9:30 AM CDT
    def test_anomaly_detection_prorated(self, sample_cost_data_periods):
        # At 9:30 AM, we're 9.5 hours into the day
        # Yesterday's EC2 was $100 for full day, so prorated = $100 * (9.5/24) = $39.58
        # For anomaly: need both >50% and >$50 increase
        
        # Modify today's cost to trigger anomaly
        # Need today's cost to be > $39.58 + $50 = ~$90
        sample_cost_data_periods["today_so_far"]["123456789012"]["Amazon EC2"] = 95.0
        
        analysis = analyze_all_periods(sample_cost_data_periods)
        
        # Should detect anomaly for EC2
        assert len(analysis["anomalies"]) > 0
        anomaly = analysis["anomalies"][0]
        assert anomaly["service"] == "Amazon EC2"
        assert anomaly["today_cost"] == 95.0
        # Expected cost should be around 39.58 (100 * 9.5/24)
        assert 39 < anomaly["expected_cost"] < 40

    def test_ai_service_alert_detection(self, sample_cost_data_periods):
        # Increase Bedrock cost significantly
        sample_cost_data_periods["today_so_far"]["123456789012"]["Amazon Bedrock"] = 50.0
        
        analysis = analyze_all_periods(sample_cost_data_periods)
        
        # Should detect AI service alert
        assert len(analysis["ai_service_alerts"]) > 0
        alert = analysis["ai_service_alerts"][0]
        assert alert["service"] == "Amazon Bedrock"
        assert alert["is_ai_service"] is True


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
            "anomalies": [],
        }

        alerts = check_for_immediate_alerts(analysis)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "CRITICAL_AI_COST"
        assert "Amazon Bedrock" in alerts[0]["message"]
        assert "$150.00" in alerts[0]["message"]

    def test_extreme_increase_alert(self):
        analysis = {
            "ai_service_alerts": [],
            "anomalies": [
                {
                    "service": "Amazon EC2",
                    "delta_percent": 600.0,  # 600% increase
                    "today_cost": 50.0,  # Over $10
                    "delta": 42.0,
                    "account_id": "123456789012",
                }
            ],
        }

        alerts = check_for_immediate_alerts(analysis)
        assert len(alerts) == 1
        assert alerts[0]["type"] == "EXTREME_INCREASE"
        assert "Amazon EC2" in alerts[0]["message"]
        assert "600%" in alerts[0]["message"]


class TestEmailGeneration:
    """Test email subject and body generation"""

    def test_subject_with_alerts(self):
        analysis = {
            "periods": {
                "today_so_far": {"total": 1234.56}
            }
        }
        alerts = [{"type": "CRITICAL_AI_COST"}]

        subject = generate_email_subject(analysis, alerts)
        assert subject.startswith("🚨")
        assert "Immediate Action Required" in subject
        assert "$1234.56 Today" in subject

    def test_subject_with_anomalies(self):
        analysis = {
            "periods": {
                "today_so_far": {"total": 1234.56}
            },
            "anomalies": [{"service": "EC2"}]
        }
        alerts = []

        subject = generate_email_subject(analysis, alerts)
        assert subject.startswith("⚠️")
        assert "Anomalies Detected" in subject
        assert "$1234.56 Today" in subject

    def test_subject_normal(self):
        analysis = {
            "periods": {
                "today_so_far": {"total": 1234.56}
            },
            "anomalies": []
        }
        alerts = []

        subject = generate_email_subject(analysis, alerts)
        assert subject.startswith("✅")
        assert "$1234.56 Today" in subject

    @freeze_time("2024-06-11 14:30:00")  # 2:30 PM UTC = 9:30 AM CDT
    def test_email_body_structure(self, sample_cost_data_periods):
        # Use analyze_all_periods to get the correct structure
        analysis = analyze_all_periods(sample_cost_data_periods)
        alerts = []
        date_ranges = get_timezone_aware_dates("US/Central")

        body = generate_email_body(analysis, alerts, date_ranges, "US/Central")

        # Check header
        assert "AWS Cost Report" in body
        assert "June 11, 2024" in body
        assert "09:30 AM CDT" in body

        # Check four metric boxes
        assert "Today (9.5 hours)" in body
        assert "$135.00" in body
        assert "Yesterday (Full Day)" in body
        assert "$250.00" in body
        assert "Month to Date" in body
        assert "$2765.00" in body
        assert "May (Full Month)" in body  # Previous month name
        assert "$7500.00" in body

        # Check timezone info
        assert "US/Central" in body
        assert "midnight to 09:30 AM CDT" in body

        # Check AI service note
        assert "Yellow highlighted rows indicate AI services" in body


@mock_aws
class TestLambdaHandler:
    """Test the main lambda_handler function"""

    @freeze_time("2024-06-11 13:00:00")  # 1 PM UTC = 8 AM CDT
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

            # Mock CE response for different time periods
            def ce_response_side_effect(**kwargs):
                # Return different data based on the date range
                if kwargs["Granularity"] == "HOURLY":
                    # Today's data (hourly)
                    return {
                        "ResultsByTime": [
                            {
                                "TimePeriod": {"Start": "2024-06-11T00:00:00Z", "End": "2024-06-11T01:00:00Z"},
                                "Groups": [
                                    {
                                        "Keys": ["Amazon EC2", "123456789012"],
                                        "Metrics": {"UnblendedCost": {"Amount": "5.0"}},
                                    }
                                ]
                            },
                            {
                                "TimePeriod": {"Start": "2024-06-11T01:00:00Z", "End": "2024-06-11T02:00:00Z"},
                                "Groups": [
                                    {
                                        "Keys": ["Amazon EC2", "123456789012"],
                                        "Metrics": {"UnblendedCost": {"Amount": "5.0"}},
                                    }
                                ]
                            }
                        ]
                    }
                else:
                    # Daily data
                    return {
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

            mock_ce.get_cost_and_usage.side_effect = ce_response_side_effect

            # Import the module to apply mocks
            import lambda_function

            lambda_function.ce_client = mock_ce
            lambda_function.ses_client = mock_ses_real
            lambda_function.org_client = mock_org_real

            result = lambda_function.lambda_handler({}, lambda_context)

            assert result["statusCode"] == 200
            assert "Cost report sent successfully" in result["body"]

            # Verify CE was called for all four periods
            assert mock_ce.get_cost_and_usage.call_count == 4

    def test_error_handling(self, lambda_context, mock_env_vars):
        with patch("lambda_function.get_organization_accounts") as mock_get_accounts:
            mock_get_accounts.side_effect = Exception("API Error")

            with patch("lambda_function.send_error_email") as mock_send_error:
                with pytest.raises(Exception):
                    lambda_handler({}, lambda_context)

                mock_send_error.assert_called_once()
                assert "API Error" in mock_send_error.call_args[0][0]


class TestTimezoneEdgeCases:
    """Test timezone edge cases and DST transitions"""

    @freeze_time("2024-03-10 07:00:00")  # 7 AM UTC on DST transition day
    def test_dst_spring_forward(self):
        # US/Eastern transitions from EST to EDT at 2 AM on March 10, 2024
        dates = get_timezone_aware_dates("US/Eastern")
        
        # Should handle the transition correctly
        assert dates["yesterday_full"][0] == "2024-03-09"
        assert dates["yesterday_full"][1] == "2024-03-10"

    @freeze_time("2024-11-03 07:00:00")  # 7 AM UTC on DST transition day
    def test_dst_fall_back(self):
        # US/Eastern transitions from EDT to EST at 2 AM on November 3, 2024
        dates = get_timezone_aware_dates("US/Eastern")
        
        # Should handle the transition correctly
        assert dates["yesterday_full"][0] == "2024-11-02"
        assert dates["yesterday_full"][1] == "2024-11-03"

    def test_non_dst_timezone(self):
        # Arizona doesn't observe DST
        dates = get_timezone_aware_dates("US/Arizona")
        assert dates is not None

    def test_international_timezones(self):
        # Test various international timezones
        for tz in ["Europe/London", "Asia/Tokyo", "Australia/Sydney"]:
            dates = get_timezone_aware_dates(tz)
            assert dates is not None
            assert len(dates) == 4  # All four periods


class TestEmailContentAccuracy:
    """Test that email content matches the new data structure"""

    @freeze_time("2024-06-11 18:00:00")  # 6 PM UTC = 1 PM CDT
    def test_email_schedule_text(self):
        # The email should mention the correct schedule
        analysis = {
            "periods": {
                "today_so_far": {"total": 100.0, "accounts": {}},
                "yesterday_full": {"total": 200.0, "accounts": {}},
                "month_to_date": {"total": 3000.0, "accounts": {}},
                "previous_month_full": {"total": 6000.0, "accounts": {}}
            },
            "anomalies": [],
            "ai_service_alerts": []
        }
        
        date_ranges = get_timezone_aware_dates("US/Central")
        body = generate_email_body(analysis, [], date_ranges, "US/Central")
        
        # Should NOT mention "every 6 hours" anymore
        assert "every 6 hours" not in body
        
        # Should show timezone info
        assert "US/Central timezone" in body
        
        # Should show today's coverage correctly
        assert "Today (13.0 hours)" in body  # 1 PM = 13 hours