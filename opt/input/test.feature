Scenario:User opens Google homepageUser opens Google homepage
    Given I have a web browser open
    When I navigate to "https://www.google.com"
    Then I should see the Google homepage
    And I should see a search bar
    And the page title should contain "Google"