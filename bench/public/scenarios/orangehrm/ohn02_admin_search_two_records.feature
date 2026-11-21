@app:orangehrm @search @negative
Feature: OrangeHRM
  Scenario: Admin search expects two records
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/admin/viewSystemUsers"
      And I fill "Admin" in "Username"
      And I click the "Search" button
    Then I should see "(2) Records Found"
