# Composio Gmail Agent Integration

## Overview

The TestZeus Hercules framework now includes a Composio agent for Gmail operations. This agent can authenticate users and fetch emails using the `GMAIL_FETCH_EMAILS` tool.

## Setup

### 1. Environment Variables

Set these environment variables:

```bash
export COMPOSIO_ENABLED=true
export COMPOSIO_API_KEY=ak_rzcfpaqxV6L1Ap5Lg1K0
export COMPOSIO_USER_ID=shahal@testzeus.com
```

### 2. Dependencies

The integration uses only the basic `composio` package (already included in pyproject.toml):

```toml
composio = "^0.8.9"
```

## Usage in Test Features

### Gherkin Feature Example

```gherkin
Feature: Gmail Email Testing
  As a test automation engineer
  I want to verify email functionality
  So that I can ensure email workflows work correctly

  Scenario: Check for recent emails
    Given I have access to Gmail through Composio
    When I fetch the latest 5 emails
    Then I should see at least 1 email in the response
    And the email data should include sender and subject information
```

### Planner Usage

The high-level planner can delegate Gmail tasks to the Composio agent:

```json
{
  "plan": "1. Check Composio Gmail connectivity\n2. Fetch latest emails\n3. Validate email structure",
  "next_step": "Check Gmail connectivity status and verify Composio tools are available for the user",
  "target_helper": "composio",
  "terminate": "no"
}
```

## Available Tools

### 1. `check_composio_status`
Validates Composio configuration and Gmail tool availability.

**Example response:**
```json
{
  "status": "success",
  "details": {
    "composio_enabled": true,
    "api_key_configured": true,
    "user_id_configured": true,
    "gmail_connected": true,
    "available_tools": ["GMAIL_FETCH_EMAILS"],
    "user_id": "shahal@testzeus.com"
  }
}
```

### 2. `fetch_gmail_emails`
Fetches emails using Composio's GMAIL_FETCH_EMAILS tool.

**Parameters:**
- `max_results` (int): Maximum emails to fetch (default: 10)
- `query` (str): Gmail search query (optional, e.g., "is:unread", "from:sender@example.com")
- `include_body` (bool): Include email body content (default: True)

**Example usage in planner:**
```
"Fetch the latest 5 emails from Gmail and verify at least one email contains 'invoice' in the subject"
```

**Example response:**
```json
{
  "status": "success",
  "execution_time": 1.23,
  "email_count": 5,
  "emails": {
    "messages": [...]
  },
  "query_used": "is:inbox",
  "max_results": 5
}
```

## Architecture Integration

### Agent Structure
- **ComposioNavAgent**: Main agent class (extends BaseNavAgent)
- **composio_nav_executor**: Executor for running Composio tools
- **composio_tools.py**: Tool implementations

### Framework Integration
- Added to `simple_hercules.py` agent initialization
- Added to `high_level_planner_agent.py` as target helper
- Follows existing agent patterns (browser, api, sql, etc.)

## Authentication Notes

1. **Tool Availability**: The agent checks if Gmail tools are available for the configured user
2. **Authentication Flow**: Uses the provided auth config ID for Gmail access
3. **Error Handling**: Gracefully handles authentication failures and missing tools
4. **Logging**: All operations are logged for debugging and audit purposes

## Testing

The integration includes:
- Status checking to verify configuration
- Tool availability validation
- Proper error handling for authentication issues
- Comprehensive logging for debugging

The Composio agent is now ready to be used in your test automation workflows alongside the existing browser, API, SQL, and other agents!
