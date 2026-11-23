@app:parabank @login @negative
Feature: ParaBank
  Scenario: Wrong password should not show the overview
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "not-the-password" in "Password"
      And I click the "Log In" button
    Then I should see "Accounts Overview"
