@app:saucedemo @cart @needs_llm
Feature: Swag Labs
  Scenario: Shopper removes one of two items from the cart
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/inventory-item.html?id=4"
      And I click the "Add to cart" button
      And I open "/inventory-item.html?id=0"
      And I click the "Add to cart" button
    When they open the cart and remove the "Sauce Labs Backpack" from it
    Then I should see "Sauce Labs Bike Light"
      And I should not see "Sauce Labs Backpack"
