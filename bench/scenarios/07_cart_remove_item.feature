@table
Feature: Cart
  Scenario: Removing an item updates the cart
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    And I open "/product.html?id=5"
    And I click the "Add to cart" button
    When I open "/cart.html"
    And I click the "Remove Classic Tee" button
    Then I should not see "Classic Tee"
    And I should see "Leather Wallet"
    And I should see "Total: $40.00"
