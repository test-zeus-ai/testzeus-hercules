@app:saucedemo @form @arithmetic
Feature: Swag Labs
  Scenario: Checkout overview adds 8% tax
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/inventory-item.html?id=4"
      And I click the "Add to cart" button
      And I open "/inventory-item.html?id=1"
      And I click the "Add to cart" button
      And I open "/cart.html"
      And I click the "Checkout" button
      And I fill "Ada" in "First Name"
      And I fill "Lovelace" in "Last Name"
      And I fill "10001" in "Zip/Postal Code"
      And I click the "Continue" button
    Then I should see "Checkout: Overview"
      And I should see "Item total: $45.98"
      And I should see "Tax: $3.68"
      And I should see "Total: $49.66"
