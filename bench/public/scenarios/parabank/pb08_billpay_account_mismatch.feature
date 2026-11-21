@app:parabank @form @validation
Feature: ParaBank
  Scenario: Bill pay rejects mismatched account numbers and a bad amount
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Bill Pay" link
      And I fill "12345" in "Account #"
      And I fill "54321" in "Verify Account #"
      And I fill "abc" in "Amount"
      And I click the "Send Payment" button
    Then I should see "The account numbers do not match."
      And I should see "Please enter a valid amount."
