@app:saucedemo @login @validation
Feature: Swag Labs
  Scenario: Locked-out user sees the lockout message
    Given I open "/"
    When I fill "locked_out_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    Then I should see "Epic sadface: Sorry, this user has been locked out."
      And I should not see "Add to cart"
