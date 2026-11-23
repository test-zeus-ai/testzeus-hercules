@app:orangehrm @form @validation
Feature: OrangeHRM
  Scenario: Leave List rejects a From Date after the To Date
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
      And I open "/web/index.php/leave/viewLeaveList"
      And I fill "2026-31-12" in "From Date"
      And I fill "2026-01-01" in "To Date"
      And I click the "Search" button
    Then I should see "To date should be after from date"
