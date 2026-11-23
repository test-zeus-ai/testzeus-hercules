@app:orangehrm @search @table
Feature: OrangeHRM
  Scenario: PIM search for an unknown name finds nothing
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/pim/viewEmployeeList"
      And I fill "Zzqx Nobody" in "Employee Name"
      And I click the "Search" button
    Then I should see "No Records Found"
