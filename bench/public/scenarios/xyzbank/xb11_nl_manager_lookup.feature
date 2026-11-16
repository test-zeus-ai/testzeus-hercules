@app:xyzbank @search @table @needs_llm
Feature: XYZ Bank
  Scenario: Bank manager looks a customer up by post code
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When the bank manager opens the customer list and searches for post code "E55656"
    Then I should see "Dumbledore"
      And I should not see "Weasly"
