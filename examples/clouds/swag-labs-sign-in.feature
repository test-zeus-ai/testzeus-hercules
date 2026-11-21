@demo @clouds
Feature: Swag Labs
  Scenario: Shopper signs in and sorts products by price
    Given I open "/"
    When I fill "standard_user" in "Username"
      And I fill "secret_sauce" in "Password"
      And I click the "Login" button
    Then I should see "Products"
    When I select "Price (low to high)" from the "Sort products" dropdown
    Then I should see "$7.99"
