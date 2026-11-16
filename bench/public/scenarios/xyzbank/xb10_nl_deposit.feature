@app:xyzbank @form @arithmetic @needs_llm
Feature: XYZ Bank
  Scenario: Ron deposits money
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When Ron Weasly logs in as a customer
      And he deposits 500 dollars into his account
    Then the balance reads "Balance : 500"
      And I should see "Deposit Successful"
