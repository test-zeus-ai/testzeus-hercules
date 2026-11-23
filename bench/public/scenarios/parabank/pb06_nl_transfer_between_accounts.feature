@app:parabank @form @needs_llm @dropdown
Feature: ParaBank
  Scenario: Customer moves money between two of their accounts
    Given I open "/parabank/index.htm"
    When I fill "john" in "Username"
      And I fill "demo" in "Password"
      And I click the "Log In" button
    When they open Transfer Funds and move "5" dollars from the first account to the second account in the lists
    Then I should see "Transfer Complete!"
      And I should see "$5.00 has been transferred"
