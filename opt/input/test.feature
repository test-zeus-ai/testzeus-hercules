Feature: Interactive Elements on the PfizerForAll Website

Background:
  Given I am on the website "https://www.pfizerforall.com/"

Scenario: Click Sign Up Button with valid inputs
  When I fill in the "First name" with "John"
  And I fill in the "Last name" with "Doe"
  And I fill in the "Email address" with "john.doe@example.com"
  And I select "Migraine" from the "Area of Interest"
  And I click the "Sign Up" button
  Then I should see a confirmation message "Thank you for signing up!"

Scenario: Click "Health questionnaires" link
  When I click the "Health questionnaires" link
  Then I should be redirected to the Health Questionnaires page

Scenario: Click "Privacy Policy" link opens in a new tab
  When I click the "Privacy Policy" link
  Then a new tab should open with URL "https://www.pfizer.com/privacy?exitCode=pfa"

Scenario: Click "Terms of Use" link
  When I click the "Terms of Use" link
  Then I should be redirected to the Terms of Use page

Scenario: Click "Cookie Preferences" link
  When I click the "Cookie Preferences" link
  Then I should see the cookie preferences popup