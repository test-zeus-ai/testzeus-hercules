@app:orangehrm @navigation @validation
Feature: OrangeHRM
  Scenario: Forgot password page requires a username
    Given I open "/web/index.php/auth/login"
    When I click "Forgot your password?"
    Then I should see "Please enter your username to identify your account to reset your password"
      And the url should contain "/auth/requestPasswordResetCode"
    When I click the "Reset Password" button
    Then I should see "Required"
    When I click the "Cancel" button
    Then the url should contain "/auth/login"
