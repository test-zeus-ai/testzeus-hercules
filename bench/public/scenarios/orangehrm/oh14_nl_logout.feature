@app:orangehrm @navigation @needs_llm
Feature: OrangeHRM
  Scenario: Admin logs out through the user menu
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
    When they open the user menu in the top right corner and choose "Logout"
    Then the url should contain "/auth/login"
      And I should see "Forgot your password?"
