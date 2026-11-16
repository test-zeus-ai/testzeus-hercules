@app:parabank @form @negative
Feature: ParaBank
  Scenario: Empty bill pay is not completed
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Bill Pay" link
      And I click the "Send Payment" button
    Then I should see "Bill Payment Complete"
