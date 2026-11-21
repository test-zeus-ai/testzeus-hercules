@app:orangehrm @login @slow
Feature: OrangeHRM
  Scenario: Admin signs in
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
    Then I should see "Dashboard"
      And the url should contain "/web/index.php/dashboard/index"
