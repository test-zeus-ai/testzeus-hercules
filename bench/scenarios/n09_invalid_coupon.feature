@negative @shadow
Feature: Cart
  Scenario: An invalid coupon is not applied
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    When I open "/cart.html"
    And I fill "BOGUS" in "Coupon code"
    And I click the "Apply" button
    Then I should see "Coupon applied"
