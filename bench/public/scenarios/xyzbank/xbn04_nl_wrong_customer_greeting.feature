@app:xyzbank @login @negative @needs_llm
Feature: XYZ Bank
  Scenario: Welcome banner names the customer who logged in
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When Albus Dumbledore logs in as a customer
    Then the welcome banner greets "Harry Potter"
