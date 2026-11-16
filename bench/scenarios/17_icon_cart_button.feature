@icon @navigation
Feature: Site header
  Scenario: The icon-only cart button opens the cart
    Given I open "/products.html"
    When I click the "Open cart" button
    Then the url should contain "/cart.html"
    And I should see "Your cart is empty"
