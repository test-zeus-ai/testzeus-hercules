@app:xyzbank @form @validation
Feature: XYZ Bank
  Scenario: Withdrawing more than the balance is refused
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Customer Login" button
      And I select "Harry Potter" from the "Your Name" dropdown
      And I click the "Login" button
      And I click the "Deposit" button
      And I fill "50" in "Amount to be Deposited"
      And I press Enter
      And I click the "Withdrawl" button
      And I fill "100" in "Amount to be Withdrawn"
      And I press Enter
    Then I should see "Transaction Failed. You can not withdraw amount more than the balance."
      And I should see "Balance : 50"
