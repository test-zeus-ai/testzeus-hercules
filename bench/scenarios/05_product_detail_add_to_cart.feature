@navigation @toast @async
Feature: Product detail
  Scenario: Open a product and add it to the cart
    Given I open "/products.html"
    When I click the "Running Shoes" link
    Then I should see "Lightweight running shoes with a cushioned sole"
    And the url should contain "product.html?id=3"
    When I click the "Add to cart" button
    Then I should see "Added to cart"
    And I should see "Cart (1)"
