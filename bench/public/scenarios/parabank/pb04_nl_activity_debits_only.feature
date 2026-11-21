@app:parabank @table @needs_llm
Feature: ParaBank
  Scenario: Customer filters account activity to debits
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
    When they open the first account listed in the Accounts Overview table
      And they filter that account's activity to show only debit transactions
    Then I should see "Account Details"
      And I should not see "Funds Transfer Received"
