@negative @table
Feature: Cart
  Scenario: An item that was never added is not in the cart
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    When I open "/cart.html"
    Then I should see "Leather Wallet"
