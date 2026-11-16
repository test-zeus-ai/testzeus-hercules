@app:saucedemo @cart
Feature: Swag Labs
  Scenario: Cart badge counts two items
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/inventory-item.html?id=4"
      And I click the "Add to cart" button
      And I open "/inventory-item.html?id=0"
      And I click the "Add to cart" button
    Then the "Cart, 2 items" button should be visible
