@app:saucedemo @login
Feature: Swag Labs
  Scenario: Standard user signs in
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    Then I should see "Products"
      And the url should contain "/inventory.html"
