@negative
Feature: Product detail
  Scenario: A button that does not exist
    Given I open "/product.html?id=1"
    When I click the "Buy now with one click" button
    Then I should see "Order placed"
