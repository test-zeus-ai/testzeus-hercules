@modal @form
Feature: Checkout
  Scenario: Cancelling the confirmation dialog keeps the shopper on checkout
    Given I open "/product.html?id=1"
    And I click the "Add to cart" button
    And I open "/checkout.html"
    When I fill "Sam Lee" in "Full name"
    And I fill "sam@example.com" in "Email"
    And I fill "555 0100 200" in "Phone"
    And I fill "1 Main Street" in "Street address"
    And I select "Germany" from the "Country" dropdown
    And I check "I agree to the terms and conditions"
    Then I should see "Order total: $24.00"
    When I click the "Place order" button
    Then I should see "Confirm your order"
    When I click the "Cancel" button
    Then I should not see "Confirm your order"
    And the url should contain "/checkout.html"
