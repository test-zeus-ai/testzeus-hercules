@app:orangehrm @navigation @needs_llm
Feature: OrangeHRM
  Scenario: Admin opens the Leave List from Quick Launch
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
    When they use the Quick Launch tile on the dashboard to open the "Leave List"
    Then the url should contain "/leave/viewLeaveList"
      And I should see "Show Leave with Status"
