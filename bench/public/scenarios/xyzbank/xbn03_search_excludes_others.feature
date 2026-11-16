@app:xyzbank @search @negative
Feature: XYZ Bank
  Scenario: Customer search hides other customers
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Bank Manager Login" button
      And I click the "Customers" button
      And I fill "Longbottom" in "Search Customer"
    Then I should see "Hermoine"
