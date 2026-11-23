@app:parabank @navigation @needs_llm
Feature: ParaBank
  Scenario: Customer logs out
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
    When they log out of online banking
    Then I should see "Customer Login"
      And I should not see "Accounts Overview"
