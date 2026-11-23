@app:saucedemo @sort @needs_llm
Feature: Swag Labs
  Scenario: Shopper sorts by highest price first
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    When they sort the products so the most expensive item comes first
    Then the first product listed is "Sauce Labs Fleece Jacket" at "$49.99"
