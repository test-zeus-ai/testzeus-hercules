@form
Feature: Sign in
  Scenario: Registered shopper signs in
    Given I open "/login.html"
    When I fill "demo@example.com" in "Email"
    And I fill "demo1234" in "Password"
    And I click the "Sign in" button
    Then I should see "Signed in as demo@example.com"
    And the url should contain "/products.html"
