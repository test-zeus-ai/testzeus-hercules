@app:orangehrm @search @negative @needs_llm
Feature: OrangeHRM
  Scenario: Unknown employee is reported as found
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/pim/viewEmployeeList"
    When they search the employee list for someone called "Zzqx Nobody"
    Then the search reports "(1) Record Found"
