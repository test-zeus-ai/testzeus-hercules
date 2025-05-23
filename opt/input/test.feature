Feature: Verification of Elements on https://www.pfizerforall.com/

  Background:
    Given the user navigates to "https://www.pfizerforall.com/"


  @heading @visibility
  Scenario: Menu Level 1 > Section Title "Get Help With"
    Given the user is on the homepage
    When the user opens the "menu" section
    Then the user should see the heading "Get Help With"
