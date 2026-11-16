@app:orangehrm @navigation @needs_llm
Feature: OrangeHRM
  Scenario: Admin views their own personal details
    Given I open "/web/index.php/auth/login"
    When I fill "Admin" in "Username"
      And I fill "admin123" in "Password"
      And I click the "Login" button
    When they open their own employee profile from the side menu
    Then I should see "Personal Details"
      And I should see "Employee Full Name"
      And the url should contain "/pim/viewPersonalDetails"
