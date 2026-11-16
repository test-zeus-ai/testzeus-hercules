@app:xyzbank @form @table
Feature: XYZ Bank
  Scenario: Bank manager adds a customer
    Given I open "/angularJs-protractor/BankingProject/#/login"
    When I click the "Bank Manager Login" button
      And I click the "Add Customer" button
      And I fill "Luna" in "First Name"
      And I fill "Lovegood" in "Last Name"
      And I fill "E1 7AA" in "Post Code"
      And I press Enter
      And I click the "Customers" button
      And I fill "Luna" in "Search Customer"
    Then I should see "Lovegood"
      And I should see "E1 7AA"
