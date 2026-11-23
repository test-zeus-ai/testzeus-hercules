@form
Feature: Checkout
  Scenario: Inline field errors appear and clear
    Given I open "/checkout.html"
    When I fill "not-an-email" in "Email"
    And I press the "Tab" key
    Then I should see "Enter a valid email address"
    When I fill "12" in "Phone"
    And I press the "Tab" key
    Then I should see "Enter a valid phone number"
    When I fill "shopper@example.com" in "Email"
    Then I should not see "Enter a valid email address"
