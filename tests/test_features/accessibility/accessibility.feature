Feature: Check accessibility for brokerage calculator on Zerodha website

  # This feature tests the brokerage calculator, and contract notes on Zerodha website

  Scenario Outline: Check calculations on brokerage and SEBI charges
    Given the user is on "https://zerodha.com/brokerage-calculator#tab-equities"
    then validate the page for accessibility.
    Then there should not be any issues.