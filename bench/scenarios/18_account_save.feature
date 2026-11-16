@form
Feature: Account
  Scenario: A saved display name greets the shopper on the home page
    Given I open "/account.html"
    When I fill "Sam" in "Display name"
    And I click the "Save" button
    Then I should see "Profile saved"
    When I open "/index.html"
    Then I should see "Hello, Sam"
