Feature: Lead Creation
  Scenario: User creates a new lead via API source and validate creation.
    Given I am logged into Salesforce url: URL with valid credentials username, password
    When I create a lead via app launcher with Indian Dummy data via UI.
    Then a lead should be created.