@app:saucedemo @login @validation
Feature: Swag Labs
  Scenario: Wrong password is rejected
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "not_the_password" in "Password"
      And I click the "Login" button
    Then I should see "Epic sadface: Username and password do not match any user in this service"
