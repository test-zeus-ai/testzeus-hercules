@app:orangehrm @navigation @negative
Feature: OrangeHRM
  Scenario: Reset link is not sent without submitting
    Given I open "/web/index.php/auth/login"
    When I click "Forgot your password?"
    Then I should see "Reset Password link sent successfully"
