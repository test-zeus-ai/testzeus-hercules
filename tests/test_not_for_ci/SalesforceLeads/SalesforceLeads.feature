Feature: Lead Creation
  Scenario: User creates a new lead via API source and validate creation.
    Given I am logged into Salesforce with valid credentials
    When I navigate to the "Leads" tab via App launcher
    And I click on the "New" button to open the lead creation form
    And I enter the leads details from data of USERS_API first record
    And I click on the "Save" button
    Then the lead should be successfully created and saved in Salesforce
    And the lead detail page should display all entered information accurately
    And any associated workflows or triggers should execute correctly