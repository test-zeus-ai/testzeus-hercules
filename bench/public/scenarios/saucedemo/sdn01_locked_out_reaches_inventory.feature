@app:saucedemo @login @negative
Feature: Swag Labs
  Scenario: Locked-out user should not reach the catalogue
    Given I open "/"
    When I fill "locked_out_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    Then I should see "Products"
