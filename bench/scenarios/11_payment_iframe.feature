@iframe
Feature: Checkout
  Scenario: Card details are saved in the embedded payment frame
    Given I open "/checkout.html"
    When I fill "4242 4242 4242 4242" in "Card number"
    And I fill "12/30" in "Expiry (MM/YY)"
    And I fill "123" in "CVC"
    And I click the "Save card" button
    Then I should see "Card saved"
    And I should see "Payment method: card ending 4242"
