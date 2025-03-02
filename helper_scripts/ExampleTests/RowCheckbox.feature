Feature: checkbox selection from list
  Scenario: User selects and deselects the checkboxes on opportunity list page
    Given I am logged into Salesforce Opportunity URL with valid credentials
    And on the opportunities list page I select the box for a row with Opportunity Name as "updatedOpps" and Account Name as empty
    Then I click on edit list.