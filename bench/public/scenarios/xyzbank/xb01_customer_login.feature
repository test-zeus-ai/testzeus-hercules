@app:xyzbank @login @dropdown
Feature: XYZ Bank
  Scenario: Customer logs in by choosing a name
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Customer Login" button
      And I select "Harry Potter" from the "Your Name" dropdown
      And I click the "Login" button
    Then I should see "Welcome Harry Potter !!"
      And I should see "Account Number : 1004"
