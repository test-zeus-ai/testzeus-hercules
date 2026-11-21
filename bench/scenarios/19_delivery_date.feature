@form @date
Feature: Checkout
  Scenario: Pick a delivery date with the date input
    Given I open "/checkout.html"
    When I fill "2026-12-01" in "Delivery date"
    Then I should see "Delivery date: 2026-12-01"
