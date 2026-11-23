@app:parabank @login @validation
Feature: ParaBank
  Scenario: Empty login asks for credentials
    Given I open "/parabank/index.htm"
    When I click the "Log In" button
    Then I should see "Please enter a username and password."
