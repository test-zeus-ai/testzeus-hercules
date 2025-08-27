Feature: Test MCP Server Connection and Math Tools
  Background:
    Given MCP integration is enabled
    And MCP server configuration exists
  
  Scenario: Connect to MCP server and perform calculations
    When I check for configured MCP servers
    Then I should see the "streamable_test" server configured
    When I initialize connections to MCP servers  
    Then the "streamable_test" server should be connected
    Then using fibonacci tool it should find the 25th fibonacci number