@app:orangehrm @dropdown_custom @search @needs_llm
Feature: OrangeHRM
  Scenario: Admin lists ESS users
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/admin/viewSystemUsers"
    When they filter the system users by the "ESS" user role and run the search
    Then the results show users whose role is "ESS"
      And I should see "Records Found"
