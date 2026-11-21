@needs_llm @form
Feature: Product catalogue (natural phrasing)
  Scenario: Shopper narrows the catalogue to in-stock accessories
    Given I open "/products.html"
    When the shopper limits the catalogue to accessories that are in stock
    And sorts them from the most expensive to the cheapest
    Then the page should show "Showing 3 products"
