@app:saucedemo @form @validation
Feature: Swag Labs
  Scenario: Checkout requires a first name
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/inventory-item.html?id=4"
      And I click the "Add to cart" button
      And I open "/cart.html"
      And I click the "Checkout" button
      And I click the "Continue" button
    Then I should see "Error: First Name is required"
