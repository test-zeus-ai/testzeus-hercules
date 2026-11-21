@app:parabank @login @table
Feature: ParaBank
  Scenario: Demo customer signs in to the accounts overview
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
    Then I should see "Accounts Overview"
      And I should see "Balance includes deposits that may be subject to holds"
      And the url should contain "/parabank/overview.htm"
