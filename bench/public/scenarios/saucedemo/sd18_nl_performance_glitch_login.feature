@app:saucedemo @login @slow @needs_llm
Feature: Swag Labs
  Scenario: Slow user still reaches the catalogue
    Given I open "/"
    When they sign in as performance_glitch_user with the password secret_sauce
    Then I should see "Products"
      And the url should contain "/inventory.html"
