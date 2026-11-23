@form @hover
Feature: Cart
  Scenario: Changing a quantity updates the totals
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    And I open "/product.html?id=5"
    And I click the "Add to cart" button
    When I open "/cart.html"
    And I fill "3" in "Quantity for Classic Tee"
    Then I should see "Subtotal: $92.00"
    And I should see "Total: $92.00"
    And I should see "Cart (4)"
    When I hover over the "Shipping info" icon
    Then I should see "Free shipping on orders over $50"
