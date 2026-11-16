@app:parabank @search @validation
Feature: ParaBank
  Scenario: Find by amount rejects a non-number
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Find Transactions" link
      And I fill "abc" in "Find by Amount"
      And I click the "Find Transactions" button under "Find by Amount"
    Then I should see "Invalid amount"
