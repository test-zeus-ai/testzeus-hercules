@app:orangehrm @login @validation
Feature: OrangeHRM
  Scenario: Wrong password shows Invalid credentials
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "not-the-password" in "Password"
      And I click the "Login" button
    Then I should see "Invalid credentials"
      And the url should contain "/auth/login"
