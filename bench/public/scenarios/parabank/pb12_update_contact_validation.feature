@app:parabank @form @validation @async
Feature: ParaBank
  Scenario: Update Contact Info requires a first name
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Update Contact Info" link
    Then I should see "Update Profile"
    When I fill "" in "First Name"
      And I click the "Update Profile" button
    Then I should see "First name is required."
