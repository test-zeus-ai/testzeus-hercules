@app:parabank @form @validation
Feature: ParaBank
  Scenario: Bill pay with an empty form lists required fields
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Bill Pay" link
      And I click the "Send Payment" button
    Then I should see "Payee name is required."
      And I should see "Zip Code is required."
      And I should see "The amount cannot be empty."
