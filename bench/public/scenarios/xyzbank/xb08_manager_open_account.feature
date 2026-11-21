@app:xyzbank @form @dropdown @table
Feature: XYZ Bank
  Scenario: Bank manager opens an account in pounds
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Bank Manager Login" button
      And I click the "Open Account" button
      And I select "Hermoine Granger" from the "Customer" dropdown
      And I select "Pound" from the "Currency" dropdown
      And I click the "Process" button
      And I click the "Customers" button
      And I fill "Granger" in "Search Customer"
    Then I should see "1001 1002 1003 1016"
