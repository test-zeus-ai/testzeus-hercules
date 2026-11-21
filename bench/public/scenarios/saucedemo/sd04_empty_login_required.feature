@app:saucedemo @login @validation @form
Feature: Swag Labs
  Scenario: Empty sign-in form asks for username then password
    Given I open "/"
    When I click the "Login" button
    Then I should see "Epic sadface: Username is required"
    When I fill "standard_user" in "Username"
      And I click the "Login" button
    Then I should see "Epic sadface: Password is required"
