@app:orangehrm @dropdown_custom @table @search
Feature: OrangeHRM
  Scenario: PIM filter by employment status
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/pim/viewEmployeeList"
      And I select "Full-Time Permanent" from the "Employment Status" dropdown
      And I click the "Search" button
    Then I should see "Russel"
      And I should see "Full-Time Permanent"
