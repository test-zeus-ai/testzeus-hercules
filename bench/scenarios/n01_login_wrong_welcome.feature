@negative @form
Feature: Sign in
  Scenario: Wrong expected text after a successful sign-in
    Given I open "/login.html"
    When I fill "demo@example.com" in "Email"
    And I fill "demo1234" in "Password"
    And I click the "Sign in" button
    Then I should see "Welcome back, administrator"
