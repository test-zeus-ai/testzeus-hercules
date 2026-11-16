@scroll
Feature: Help
  Scenario: Expand a question far below the fold
    Given I open "/faq.html"
    When I click the "Do you ship internationally?" button
    Then I should see "We ship to 40 countries"
