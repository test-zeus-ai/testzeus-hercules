@app:saucedemo @negative @needs_llm
Feature: Swag Labs
  Scenario: problem_user product photos are all the same placeholder
    Given I open "/"
    When I fill "problem_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    Then the product card for "Sauce Labs Backpack" shows a photo of the backpack rather than a placeholder image
