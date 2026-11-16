@app:xyzbank @form @arithmetic
Feature: XYZ Bank
  Scenario: Deposit updates the balance
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Customer Login" button
      And I select "Harry Potter" from the "Your Name" dropdown
      And I click the "Login" button
      And I click the "Deposit" button
      And I fill "250" in "Amount to be Deposited"
      And I press Enter
    Then I should see "Deposit Successful"
      And I should see "Balance : 250"
