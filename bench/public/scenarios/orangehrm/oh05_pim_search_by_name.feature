@app:orangehrm @search @table @async
Feature: OrangeHRM
  Scenario: PIM employee search by name
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/pim/viewEmployeeList"
      And I fill "Russel" in "Employee Name"
      And I click the "Search" button
    Then I should see "Hamilton"
      And I should see "Record Found"
