@app:parabank @form @negative @needs_llm
Feature: ParaBank
  Scenario: Loan form has no friendly validation
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
    When they submit the loan request form without filling anything in
    Then the form says "Loan amount is required"
