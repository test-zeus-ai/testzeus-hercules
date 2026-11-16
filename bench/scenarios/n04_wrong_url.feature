@negative @form
Feature: Sign in
  Scenario: Wrong URL expected after sign-in
    Given I open "/login.html"
    When I fill "demo@example.com" in "Email"
    And I fill "demo1234" in "Password"
    And I click the "Sign in" button
    Then the url should contain "/dashboard"
