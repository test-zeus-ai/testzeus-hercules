Feature: User Signup and Account Deletion

  Scenario: Launch the website and verify home page visibility
    Given the user has launched the browser
    When the user navigates to "http://automationexercise.com"
    Then the home page should be visible

  Scenario: Signup page
    Given the user is on the home page
    When the user clicks on the "Signup / Login" button, 
    Then the "New User Signup!" message should be visible
    Given the user is on the "Signup / Login" page
    When the user enters a name and email address
    And clicks the "Signup" button
    Then the "ENTER ACCOUNT INFORMATION" form should be visible
    Given the user is on the "Signup / Login" page
    When the user enters a name and email address
    And clicks the "Signup" button
    Then the "ENTER ACCOUNT INFORMATION" form should be visible
    Given the user is on the "ENTER ACCOUNT INFORMATION" form
    When the user fills in account details, Use the first record from the test data to fill following.
      | Title         | Name     | Email       | Password | Date of birth |
      | Mr./Ms.       | [Name]   | [Email]     | [Password] | [DD/MM/YYYY] |
    And selects the checkbox "Sign up for our newsletter!"
    And selects the checkbox "Receive special offers from our partners!"
    Given the user has filled in the account information
    When the user fills in additional details,  Use the first record from the test data to fill following.
      | First name | Last name | Company  | Address  | Address2 | Country | State | City | Zipcode | Mobile Number |
      | [First]    | [Last]    | [Company] | [Address1] | [Address2] | [Country] | [State] | [City] | [Zip] | [Mobile] |
    And clicks the "Create Account" button
    Then the "ACCOUNT CREATED!" message should be visible
    Given the user has created an account
    When the user clicks the "Continue" button, the user is first record from test data.
    Then the "Logged in as [Email]" message should be visible

  Scenario: Delete the account
    Given the user is logged in as a new user, via record 1 from test data, use email instead of username
    When the user clicks on the "Delete Account" button
    Then the "ACCOUNT DELETED!" message should be visible
    Given the account is deleted
    When the user clicks the "Continue" button
    Then the user should be redirected to the home page
