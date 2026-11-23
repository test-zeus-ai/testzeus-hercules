@app:saucedemo @cart @table
Feature: Swag Labs
  Scenario: Cart lists the added products with prices
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/inventory-item.html?id=1"
      And I click the "Add to cart" button
      And I open "/inventory-item.html?id=2"
      And I click the "Add to cart" button
      And I open "/cart.html"
    Then I should see "Your Cart"
      And I should see "Sauce Labs Bolt T-Shirt"
      And I should see "$15.99"
      And I should see "Sauce Labs Onesie"
      And I should see "$7.99"
