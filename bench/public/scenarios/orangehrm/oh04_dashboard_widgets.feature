@app:orangehrm @async @slow
Feature: OrangeHRM
  Scenario: Dashboard widgets are shown
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
    Then I should see "Time at Work"
      And I should see "My Actions"
      And I should see "Quick Launch"
      And I should see "Employees on Leave Today"
      And I should see "Employee Distribution by Sub Unit"
