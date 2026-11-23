@env:pre-seeded-roboform
Feature: RoboForm form filling with minimum fields

Scenario: Fill minimum fields in RoboForm test form
  Given I open "/filling-test-all-fields"
  When I fill "Mr." in "Title" field
  And I fill "Alex" in "First Name" field
  And I fill "Morgan" in "Last Name" field
  And I fill "Alex Morgan" in "Full Name" field
  And I fill "{{email}}" in "E-mail" field
  Then I verify the "First Name" field contains "Alex"
  And I verify the "Last Name" field contains "Morgan"
  And I verify the "Full Name" field contains "Alex Morgan"
  And I verify the "E-mail" field contains "{{email}}"
