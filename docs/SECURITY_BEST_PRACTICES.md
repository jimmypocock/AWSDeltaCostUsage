# Security Best Practices for AWS Cost Monitor

## Email Security & Cost Protection

### 1. **Built-in Safety Features**

✅ **Rate Limiting**
- Maximum 10 emails per hour per Lambda execution
- Duplicate email detection (30-minute window)
- Prevents email storms and excessive SES costs

✅ **SES Quota Checking**
- Monitors your SES sending limits
- Stops sending at 80% of quota to leave buffer
- Prevents hitting SES limits

✅ **Bounce/Complaint Protection**
- Checks SES suppression list before sending
- Filters out problematic email addresses
- Prevents reputation damage

✅ **Email Validation**
- Validates email format before sending
- Filters out invalid addresses
- Reduces bounce rate

### 2. **Cost Controls**

| Control | Setting | Purpose |
|---------|---------|---------|
| Lambda Timeout | 5 minutes | Prevents runaway execution costs |
| Retry Attempts | 2 max | Limits API call costs |
| Pagination Limit | 10 pages | Prevents infinite loops |
| Schedule | 4x daily | Predictable execution count |
| Log Retention | 14 days | Limits CloudWatch storage costs |

### 3. **Estimated Monthly Costs**

```
Lambda Executions: 4 runs/day × 30 days = 120 executions
Lambda Duration: 120 × 30 seconds avg = 3,600 seconds
Lambda Memory: 512 MB

Lambda Cost: ~$0.75/month
SES Cost: 120 emails × $0.0001 = ~$0.01/month
CloudWatch Logs: ~$0.50/month

Total: < $2/month
```

### 4. **Security Checklist**

Before deploying to production:

- [ ] Verify sender email in SES
- [ ] Verify all recipient emails in SES
- [ ] Review IAM permissions (least privilege)
- [ ] Test manually with `aws lambda invoke`
- [ ] Monitor first few days of operation

### 5. **Simple Monitoring**

Since the Lambda runs only 4 times daily and sends error emails, you can simply:

```bash
# Check recent executions
aws logs tail /aws/lambda/aws-cost-monitor --follow

# View last few invocations
aws lambda list-function-event-invoke-configs --function-name aws-cost-monitor

# Check if emails are being sent
aws ses get-send-statistics --start-time 2024-01-01 --end-time 2024-12-31
```

No need for complex CloudWatch alarms - the Lambda will email you if something goes wrong!

### 6. **Emergency Shutoff**

If something goes wrong:

```bash
# Disable the EventBridge rule immediately
aws events disable-rule --name cost-monitor-daily-schedule

# Or delete the entire stack
aws cloudformation delete-stack --stack-name AWSDeltaCostUsage
```

### 7. **Advanced Production Setup**

For mission-critical deployments:

1. **Use AWS Secrets Manager**
   ```python
   import boto3
   import json
   
   def get_email_config():
       client = boto3.client('secretsmanager')
       secret = client.get_secret_value(SecretId='cost-monitor/email-config')
       return json.loads(secret['SecretString'])
   ```

2. **Dead Letter Queue**
   ```yaml
   DeadLetterQueue:
     Type: AWS::SQS::Queue
     Properties:
       QueueName: cost-monitor-dlq
       MessageRetentionPeriod: 1209600  # 14 days
   ```

3. **SNS Topics for Alerts**
   ```yaml
   AlertTopic:
     Type: AWS::SNS::Topic
     Properties:
       Subscription:
         - Endpoint: admin@example.com
           Protocol: email
   ```

### 8. **SES Best Practices**

1. **Configure DKIM**: Improves deliverability
2. **Set up SPF**: Add SES to your domain's SPF record
3. **Monitor Reputation**: Check SES reputation dashboard
4. **Handle Bounces**: Set up bounce handling endpoint
5. **Implement List-Unsubscribe**: For compliance

### 9. **Testing Safely**

Start with conservative settings:
```bash
# Test with high thresholds first
ANOMALY_THRESHOLD_PERCENT=200  # Only alert on 200% increase
ANOMALY_THRESHOLD_DOLLARS=1000  # Only alert on $1000+ increase

# Test with single recipient
EMAIL_TO=test@example.com

# Run manual test
aws lambda invoke --function-name aws-cost-monitor /tmp/test.json
```

### 10. **Common Issues & Solutions**

| Issue | Solution |
|-------|----------|
| Emails not sending | Check SES verification, sandbox mode |
| High bounce rate | Verify recipient emails, check suppression list |
| Hitting SES limits | Increase SES quota, reduce frequency |
| Lambda timeouts | Check for API throttling, increase timeout |
| No cost data | Verify Cost Explorer is enabled, wait 24h |

Remember: **Start small, monitor closely, scale gradually!**