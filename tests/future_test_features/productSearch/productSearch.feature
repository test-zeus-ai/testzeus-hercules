Feature: Check search and filtering on the Wrangler website

  # This feature tests the browser automation capabilities around navigating ecomm website

  Scenario Outline: Search for yellow jacket
    Given a user is on the URL as https://wrangler.in
    When the user clicks on Search icon
    And the user enters text as "Rainbow jacket"
    And the user selects "Turtle neck" as the filter
    Then only one product should be displayed as the result