@form
Feature: Product catalogue
  Scenario: Checkbox, select and radio filters combine
    Given I open "/products.html"
    When I check "In stock only"
    And I select "Footwear" from the "Category" dropdown
    And I click the "Price: high to low" radio button
    Then I should see "Showing 2 products"
    And I should see "Running Shoes"
    And I should not see "Denim Jacket"
