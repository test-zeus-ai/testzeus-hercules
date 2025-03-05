Feature: User edits contacts with accounts
  Scenario: User edits contacts
    Given I open URL https://testzeus2-dev-ed.lightning.force.com/lightning/o/Contact/list?filterName=__Recent
    When i create new contact. 
    and I set Account Name as "Account Created for Testing"
    and set salutation as Mrs.
    and I click on save.
    Then i should not get error for Account Name and Salutation.
