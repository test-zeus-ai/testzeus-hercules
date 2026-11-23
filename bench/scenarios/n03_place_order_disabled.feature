@negative @form
Feature: Checkout
  Scenario: Place order stays disabled while required fields are empty
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    And I open "/checkout.html"
    When I fill "Sam Lee" in "Full name"
    And I click the "Place order" button
    Then I should see "Confirm your order"
