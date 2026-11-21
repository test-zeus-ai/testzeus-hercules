@app:orangehrm @search @async @dropdown_custom
Feature: OrangeHRM
  Scenario: Directory search picks an autocomplete suggestion
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/directory/viewDirectory"
      And I fill "Peter" in "Employee Name"
      And I click the "Peter Mac Anderson" option
      And I click the "Search" button
    Then I should see "(1) Record Found"
      And I should see "Chief Financial Officer"
