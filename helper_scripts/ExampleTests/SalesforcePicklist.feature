Feature: make a new contact
  Scenario: User selects and deselects the opening opporutnity data on Contacts list page
    Given I am logged into Salesforce Contact URL with valid credentials
    And I click on new contact.
    And I search and select "Testzeus Inc" as account.
    And I fill Indian Dummy data in all relevant fields.
    Then I click on save.
    And contact should be created for account Testzeus Inc.