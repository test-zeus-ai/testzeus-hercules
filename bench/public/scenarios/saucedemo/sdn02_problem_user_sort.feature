@app:saucedemo @sort @negative
Feature: Swag Labs
  Scenario: problem_user sorting is broken
    Given I open "/"
    When I fill "problem_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I select "Price (low to high)" from the "Sort products" dropdown
    Then I should see "Price (high to low) Sauce Labs Onesie"
