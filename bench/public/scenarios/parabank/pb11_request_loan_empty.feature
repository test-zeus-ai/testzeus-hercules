@app:parabank @form @validation
Feature: ParaBank
  Scenario: Empty loan request shows the site's error page
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Request Loan" link
      And I click the "Apply Now" button
    Then I should see "An internal error has occurred and has been logged."
