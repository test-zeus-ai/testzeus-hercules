Feature: Open Google homepage 

  Scenario: User opens Google homepage
    Given I have a web browser open
    When I navigate to "https://www.google.com"
    Then I should see the Google homepage