@app:xyzbank @table @async
Feature: XYZ Bank
  Scenario: Transactions list shows the credit and the debit
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Customer Login" button
      And I select "Harry Potter" from the "Your Name" dropdown
      And I click the "Login" button
      And I click the "Deposit" button
      And I fill "75" in "Amount to be Deposited"
      And I press Enter
      And I wait 2 seconds
      And I click the "Withdrawl" button
      And I fill "25" in "Amount to be Withdrawn"
      And I press Enter
      And I wait 2 seconds
      And I click the "Transactions" button
    Then I should see "Credit"
      And I should see "Debit"
      And I should see "Transaction Type"
