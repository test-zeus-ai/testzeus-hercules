@app:parabank @login @validation
Feature: ParaBank
  Scenario: Wrong password is refused
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "not-the-password" in "Password"
      And I click the "Log In" button
    Then I should see "The username and password could not be verified."
