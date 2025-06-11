# Contributing to AWS Cost Monitor

First off, thank you for considering contributing to AWS Cost Monitor! It's people like you that make this tool better for everyone.

## Code of Conduct

By participating in this project, you are expected to uphold our Code of Conduct (see CODE_OF_CONDUCT.md).

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check existing issues to avoid duplicates. When you create a bug report, include as many details as possible:

* **Use a clear and descriptive title**
* **Describe the exact steps to reproduce the problem**
* **Provide specific examples**
* **Describe the behavior you observed and what you expected**
* **Include logs from CloudWatch if relevant**
* **Note your AWS region and any special configuration**

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion:

* **Use a clear and descriptive title**
* **Provide a detailed description of the suggested enhancement**
* **Provide specific examples to demonstrate the feature**
* **Describe the current behavior and expected behavior**
* **Explain why this enhancement would be useful**

### Pull Requests

1. Fork the repo and create your branch from `main`
2. If you've added code that should be tested, add tests
3. Ensure your code follows the existing style
4. Make sure your code lints
5. Issue that pull request!

## Development Process

1. **Setup your environment:**
   ```bash
   git clone https://github.com/yourusername/AWSDeltaCostUsage.git
   cd AWSDeltaCostUsage
   cp .env.example .env
   # Edit .env with your test configuration
   ```

2. **Make your changes:**
   - Follow the existing code style
   - Add comments for complex logic
   - Update documentation if needed

3. **Test your changes:**
   ```bash
   # Test locally
   sam local invoke CostMonitorFunction -e events/test-event.json
   
   # Deploy to test environment
   ./deploy.sh --stack-name test-cost-monitor
   
   # Invoke and check logs
   aws lambda invoke --function-name aws-cost-monitor /tmp/test-output.json
   aws logs tail /aws/lambda/aws-cost-monitor --follow
   ```

4. **Clean up test resources:**
   ```bash
   aws cloudformation delete-stack --stack-name test-cost-monitor
   ```

## Style Guidelines

### Python Style

* Follow PEP 8
* Use descriptive variable names
* Add docstrings to functions
* Keep functions focused and small
* Use type hints where appropriate

### Commit Messages

* Use the present tense ("Add feature" not "Added feature")
* Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
* Limit the first line to 72 characters or less
* Reference issues and pull requests liberally after the first line

### Documentation

* Update the README.md with details of changes to the interface
* Update CLAUDE.md if you change commands or architecture
* Add comments to explain complex logic
* Update .env.example if you add new configuration options

## Adding New Features

### Adding New AI Services to Monitor

1. Add the service name to the `AI_SERVICES` list in `src/lambda_function.py`
2. Test that costs are properly highlighted for the new service
3. Update the README.md to list the new service

### Adding New Alert Types

1. Modify the `check_for_immediate_alerts()` function
2. Add new threshold configuration to template.yaml parameters
3. Update .env.example with the new configuration
4. Document the new alert type in README.md

## Questions?

Feel free to open an issue with your question or reach out to the maintainers.

Thank you for contributing!