# Security Policy

## Supported Versions

We release patches for security vulnerabilities. Which versions are eligible for receiving such patches depends on the CVSS v3.0 Rating:

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

Please report security vulnerabilities by creating a private security advisory on GitHub:

1. Go to the Security tab of this repository
2. Click on "Report a vulnerability"
3. Fill out the form with as much detail as possible

Please include:
- Type of issue (e.g., IAM permission escalation, data exposure, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

## Security Considerations

This Lambda function requires several AWS permissions to operate. When deploying:

1. **Principle of Least Privilege**: The Lambda function only has permissions it needs:
   - Read-only access to Cost Explorer
   - Read-only access to Organizations
   - Send email via SES (restricted to verified addresses)

2. **Email Security**: 
   - Only verified email addresses can be used
   - SES permissions are restricted to the configured sender address

3. **Configuration Security**:
   - Never commit `.env` files with real email addresses
   - Use AWS Secrets Manager for production deployments if needed
   - Rotate IAM credentials regularly

4. **Data Security**:
   - Cost data remains within your AWS account
   - No data is sent to external services except email via SES
   - Email reports contain cost information - ensure recipients are authorized

## Response Timeline

We will strive to:
- Confirm receipt of your vulnerability report within 2 business days
- Provide an initial assessment within 5 business days
- Release a fix as soon as possible, depending on complexity

Thank you for helping keep AWS Cost Monitor secure!