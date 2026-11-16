@app:xyzbank @search @table
Feature: XYZ Bank
  Scenario: Bank manager searches customers
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Bank Manager Login" button
      And I click the "Customers" button
      And I fill "Longbottom" in "Search Customer"
    Then I should see "Neville"
      And I should not see "Hermoine"
