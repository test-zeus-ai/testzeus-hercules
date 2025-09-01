Feature: Test MCP Server Connection and Math Tools
  Background:
    Given MCP integration is enabled
    And MCP server configuration exists
  
  Scenario: Connect to MCP server and perform calculations
    When I check for configured MCP servers
    Then I should see the "mail" server configured
    Then search for OPT key from 'shahal@testzeus.com' mail
    