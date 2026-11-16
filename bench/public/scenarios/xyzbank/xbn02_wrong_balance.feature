@app:xyzbank @arithmetic @negative
Feature: XYZ Bank
  Scenario: Balance after withdrawal is miscalculated
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Customer Login" button
      And I select "Harry Potter" from the "Your Name" dropdown
      And I click the "Login" button
      And I click the "Deposit" button
      And I fill "300" in "Amount to be Deposited"
      And I press Enter
      And I click the "Withdrawl" button
      And I fill "120" in "Amount to be Withdrawn"
      And I press Enter
    Then I should see "Transaction successful"
      And I should see "Balance : 200"
