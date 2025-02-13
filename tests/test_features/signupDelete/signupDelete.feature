Feature: User Signup and Account Deletion

  Scenario: Signup page
    Given the user is on the home page
    And the user clicks on the "Signup / Login" button, 
    And the "New User Signup!" message should be visible
    When Using the first record from the test data. Fill the name and email for signup.
    And clicks the "Signup" button
    And the user is on the "ENTER ACCOUNT INFORMATION" form
    And the user fills in account details, Use the first record from the test data to fill all possible fields on the UI.
    And selects the checkbox "Sign up for our newsletter!"
    And selects the checkbox "Receive special offers from our partners!"
    And the user has filled in the account information
    And the user fills in additional details,  Use the first record from the test data to fill all possible fields on the UI. if data dosen't match with UI find a substitute.
    And clicks the "Create Account" button
    And the "ACCOUNT CREATED!" message should be visible
    And the user has created an account
    And the user clicks the "Continue" button, the user is first record from test data.
    Then the "Logged in as [Email]" or "Logged in as [Name]" message should be visible

  Scenario: Delete the account
    Given the user is logged in as a new user, via record 1 from test data, use email instead of username
    When the user clicks on the "Delete Account" button
    Then the "ACCOUNT DELETED!" message should be visible
    And the account is deleted
    And the user clicks the "Continue" button
    And the user should be redirected to the home page
