Feature: In line edit of opportunity
  Scenario: User edits the opportunity in line
    Given I am logged into Salesforce Opportunity URL with valid credentials
    And for row where name "updatedOpps" and account site as "My Company", I hover on it
    And I perform an inline edit of the opportunity name 
    And I update the opportunity name to new name by appending "123" to it, updates are committed only after pressing enter.
    And I click on the Save button
    Then the edited opportunity name should be displayed