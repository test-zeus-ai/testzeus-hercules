@needs_llm @form
Feature: Sign in (natural phrasing)
  Scenario: Shopper signs in with the demo account
    Given the shopper starts on the sign-in page at "/login.html"
    When they sign in with the email demo@example.com and the password demo1234
    Then they land on the catalogue showing "Signed in as demo@example.com"
