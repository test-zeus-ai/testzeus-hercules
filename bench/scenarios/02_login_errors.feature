@form @async
Feature: Sign in
  Scenario: Sign-in shows validation errors and rejects a wrong password
    Given I open "/login.html"
    When I click the "Sign in" button
    Then I should see "Email is required"
    And I should see "Password is required"
    When I fill "demo@example.com" in "Email"
    And I fill "wrong-password" in "Password"
    And I click the "Sign in" button
    Then I should see "Incorrect email or password"
    And I should not see "Signed in as"
