Feature: Lead Creation
  Scenario: User creates a new lead via API source and validate creation.
    Given I am logged into Salesforce with valid credentials
    When I navigate to the "Leads" tab via App launcher
    And I click on the "New" button to open the lead creation form
    And I enter the leads details from data of USERS_API last record, add current time to the name and all the text fields.
    And I click on the "Save" button, if there is an issue with lead creation then augment the data accordingly to create a lead.
    Then the lead should be successfully created and saved in Salesforce
    And the lead detail page should display all entered information accurately
