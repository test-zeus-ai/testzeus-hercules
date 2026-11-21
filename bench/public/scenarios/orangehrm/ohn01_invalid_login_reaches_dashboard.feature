@app:orangehrm @login @negative
Feature: OrangeHRM
  Scenario: Wrong password should not reach the dashboard
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "not-the-password" in "Password"
      And I click the "Login" button
    Then I should see "Dashboard"
