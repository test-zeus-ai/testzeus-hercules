@app:saucedemo @navigation
Feature: Swag Labs
  Scenario: Product detail page and back
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    When I click "Sauce Labs Fleece Jacket"
    Then the url should contain "/inventory-item.html?id=5"
      And I should see "$49.99"
    When I click the "Back to products" button
    Then the url should contain "/inventory.html"
