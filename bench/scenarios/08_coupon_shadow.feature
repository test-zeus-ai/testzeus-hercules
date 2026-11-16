@shadow
Feature: Cart
  Scenario: A coupon in a shadow-DOM widget discounts the order
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    When I open "/cart.html"
    And I fill "SAVE10" in "Coupon code"
    And I click the "Apply" button
    Then I should see "Coupon applied: 10% off"
    And I should see "Total: $22.10"
