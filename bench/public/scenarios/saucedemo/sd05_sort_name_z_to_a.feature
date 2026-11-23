@app:saucedemo @sort
Feature: Swag Labs
  Scenario: Sort products Z to A
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
      And I select "Name (Z to A)" from the "Sort products" dropdown
    Then I should see "Price (high to low) Test.allTheThings() T-Shirt (Red)"
