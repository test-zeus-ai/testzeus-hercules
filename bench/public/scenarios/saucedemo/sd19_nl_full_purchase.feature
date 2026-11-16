@app:saucedemo @form @needs_llm
Feature: Swag Labs
  Scenario: Shopper buys a backpack end to end
    Given I open "/"
    When they sign in as standard_user with password secret_sauce
      And they add the Sauce Labs Backpack to the cart and go to checkout
      And they enter Ada Lovelace with postal code 10001 and continue to the overview
      And they finish the order
    Then the confirmation says "Thank you for your order!"
