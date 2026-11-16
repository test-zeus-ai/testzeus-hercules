@app:orangehrm @login @validation
Feature: OrangeHRM
  Scenario: Empty sign-in form marks both fields Required
    Given I open "/web/index.php/auth/login"
    When I click the "Login" button
    Then I should see "Required"
      And the url should contain "/auth/login"
