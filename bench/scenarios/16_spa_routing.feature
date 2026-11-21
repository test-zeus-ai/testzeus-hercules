@spa @async
Feature: Store info
  Scenario: Client-side routes load their content asynchronously
    Given I open "/spa.html"
    Then I should see "Store overview"
    When I click the "Reviews" link
    Then I should see "Rated 4.6 out of 5 based on 128 reviews"
    And the url should contain "view=reviews"
    When I click the "Load more reviews" button
    Then I should see "Showing 5 of 128 reviews"
    When I click the "Shipping" link
    And I wait until "Free standard shipping" appears
    Then I should see "Express delivery arrives in 1 to 2 business days"
    And the url should contain "view=shipping"
