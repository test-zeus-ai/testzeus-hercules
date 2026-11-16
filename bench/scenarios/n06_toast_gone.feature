@negative @toast @async
Feature: Product detail
  Scenario: The confirmation toast has already disappeared
    Given I open "/product.html?id=1"
    When I click the "Add to cart" button
    And I wait for 4 seconds
    Then I should see "Added to cart"
