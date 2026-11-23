@app:parabank @form @arithmetic @negative
Feature: ParaBank
  Scenario: Transfer confirmation shows the amount actually sent
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
      And I click the "Transfer Funds" link
      And I fill "10" in "Amount"
      And I click the "Transfer" button
    Then I should see "Transfer Complete!"
      And I should see "$100.00 has been transferred"
