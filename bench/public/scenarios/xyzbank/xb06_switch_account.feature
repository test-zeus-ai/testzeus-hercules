@app:xyzbank @dropdown
Feature: XYZ Bank
  Scenario: Customer switches between their accounts
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Customer Login" button
      And I select "Harry Potter" from the "Your Name" dropdown
      And I click the "Login" button
      And I select "1006" from the account dropdown
    Then I should see "Account Number : 1006"
      And I should see "Currency : Rupee"
