# A v1 feature with a background, two scenarios and an outline
@smoke
Feature: Shop login
  Users sign in to the demo shop.

  Background:
    Given I open the page "http://localhost:3000/login"

  Scenario: Valid user signs in
    When I enter the username from test data
    And I enter the password from test data
    And I click "Sign in"
    Then the page should contain "Products"
    And the cart badge should be empty

  @negative
  Scenario Outline: Locked accounts are refused
    When I sign in as "<user>" with "<password_hint>"
    Then I should see the error "<message>"

    Examples:
      | user          | password_hint | message            |
      | locked_user   | standard      | Account is locked  |
      | blocked_user  | standard      | Account is blocked |
