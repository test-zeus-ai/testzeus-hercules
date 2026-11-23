@table
Feature: Order history
  Scenario: Page through the orders table
    Given I open "/orders.html"
    Then I should see "Page 1 of 3"
    When I click the "Next" button
    Then I should see "Page 2 of 3"
    And I should see "1015"
    And I should not see "1001"
    When I click the "Next" button
    Then I should see "Page 3 of 3"
    And I should see "1023"
