@app:saucedemo @navigation @cart
Feature: Swag Labs
  Scenario: Reset App State empties the cart, then log out
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/inventory-item.html?id=4"
      And I click the "Add to cart" button
      And I click the "Open Menu" button
      And I click the "Reset App State" button
    Then the "Cart, empty" button should be visible
    When I click the "Logout" button
    Then the url should be "https://www.saucedemo.com/"
      And I should see "Accepted usernames are:"
