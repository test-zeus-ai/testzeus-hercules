Feature: Check search and filtering on the Wrangler website
  Scenario: product search
    When I navigate to [$.testdata.A wrangler testcase.url]
    When the user clicks on Search icon
    And the user enters mens jeans in the search input field and press "Enter" key
    Then products should be displayed