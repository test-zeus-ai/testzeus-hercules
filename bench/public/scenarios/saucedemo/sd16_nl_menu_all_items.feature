@app:saucedemo @navigation @needs_llm
Feature: Swag Labs
  Scenario: Shopper returns to the catalogue from the cart through the menu
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I open "/cart.html"
    When they use the side menu to go back to all items
    Then the url should contain "/inventory.html"
      And I should see "Products"
