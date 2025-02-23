Feature: Check search and filtering on the Wrangler website

  Scenario Outline: Search for sweater
    Given a user is on the URL as https://wrangler.in
    When the user clicks on Search icon
    And on the search bar, the user enters text as "Rainbow sweater"
    And the user filters Turtle Neck within Neck filter section.
    Then only one product should be displayed as the result, regarless of type of product.