@form @icon
Feature: Product catalogue
  Scenario: Search narrows the product list and the icon button clears it
    Given I open "/products.html"
    When I fill "backpack" in "Search products"
    Then I should see "Showing 1 product"
    And I should see "Hiking Backpack"
    And I should not see "Classic Tee"
    When I click the "Clear search" button
    Then I should see "Showing 8 products"
