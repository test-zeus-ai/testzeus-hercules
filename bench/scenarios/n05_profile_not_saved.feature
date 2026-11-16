@negative @form
Feature: Account
  Scenario: A display name that was never saved does not persist
    Given I open "/account.html"
    When I fill "Sam" in "Display name"
    And I open "/index.html"
    Then I should see "Hello, Sam"
