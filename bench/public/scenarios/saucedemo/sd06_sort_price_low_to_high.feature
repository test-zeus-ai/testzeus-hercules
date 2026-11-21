@app:saucedemo @sort
Feature: Swag Labs
  Scenario: Sort products by price low to high
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I select "Price (low to high)" from the "Sort products" dropdown
    Then I should see "Price (high to low) Sauce Labs Onesie"
      And I should see "$7.99"
