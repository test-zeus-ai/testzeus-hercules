@env:pre-seeded-roboform
Feature: RoboForm form filling with all fields

Scenario: Fill all fields in RoboForm test form
  Given I open "/filling-test-all-fields"
  When I fill "Mr." in "Title" field
  And I fill "Alex" in "First Name" field
  And I fill "J" in "Middle Initial" field
  And I fill "Morgan" in "Last Name" field
  And I fill "Alex J Morgan" in "Full Name" field
  And I fill "Example Corp" in "Company" field
  And I fill "QA Engineer" in "Position" field
  And I fill "221 Market Street" in "Address Line 1" field
  And I fill "Suite 400" in "Address Line 2" field
  And I fill "Springfield" in "City" field
  And I fill "Illinois" in "State / Province" field
  And I fill "United States" in "Country" field
  And I fill "62701" in "Zip" field
  And I fill "2175550101" in "Home Phone" field
  And I fill "2175550102" in "Work Telephone" field
  And I fill "2175550103" in "Fax" field
  And I fill "2175550104" in "Cell Phone" field
  And I fill "{{email}}" in "E-mail" field
  And I fill "https://www.example.com" in "Web Site" field
  And I fill "alex_test" in "User ID" field
  And I fill "TestPassword@123" in "Password" field
  And I select "Visa (Preferred)" from "Credit Card Type" dropdown
  And I fill "4111111111111111" in "Credit Card Number" field
  And I fill "123" in "Card Verification Code" field
  And I select "12" from "Card Expiration Date Month" dropdown
  And I select "2028" from "Card Expiration Date Year" dropdown
  And I fill "Alex J Morgan" in "Card User Name" field
  And I fill "Test Bank" in "Card Issuing Bank" field
  And I fill "8005550199" in "Card Customer Service Phone" field
  And I fill "Male" in "Sex" field
  And I fill "000-00-0000" in "Social Security Number" field
  And I fill "TEST123456" in "Driver License Number" field
  And I select "15" from "Date Of Birth Day" dropdown
  And I select "Jun" from "Date Of Birth Month" dropdown
  And I select "1995" from "Date Of Birth Year" dropdown
  And I fill "31" in "Age" field
  And I fill "Springfield" in "Birth Place" field
  And I fill "75000" in "Income" field
  And I fill "RoboForm full form filling test" in "Custom Message" field
  And I fill "Testing all available form fields with sample data" in "Comments" field

  Then I verify the "Title" field contains "Mr."
  And I verify the "First Name" field contains "Alex"
  And I verify the "Middle Initial" field contains "J"
  And I verify the "Last Name" field contains "Morgan"
  And I verify the "Full Name" field contains "Alex J Morgan"
  And I verify the "Company" field contains "Example Corp"
  And I verify the "Position" field contains "QA Engineer"
  And I verify the "Address Line 1" field contains "221 Market Street"
  And I verify the "Address Line 2" field contains "Suite 400"
  And I verify the "City" field contains "Springfield"
  And I verify the "State / Province" field contains "Illinois"
  And I verify the "Country" field contains "United States"
  And I verify the "Zip" field contains "62701"
  And I verify the "Home Phone" field contains "2175550101"
  And I verify the "Work Telephone" field contains "2175550102"
  And I verify the "Fax" field contains "2175550103"
  And I verify the "Cell Phone" field contains "2175550104"
  And I verify the "E-mail" field contains "{{email}}"
  And I verify the "Web Site" field contains "https://www.example.com"
  And I verify the "User ID" field contains "alex_test"
  And I verify the "Password" field contains "TestPassword@123"
  And I verify the "Credit Card Type" dropdown has "Visa (Preferred)" selected
  And I verify the "Credit Card Number" field contains "4111111111111111"
  And I verify the "Card Verification Code" field contains "123"
  And I verify the "Card Expiration Date Month" dropdown has "12" selected
  And I verify the "Card Expiration Date Year" dropdown has "2028" selected
  And I verify the "Card User Name" field contains "Alex J Morgan"
  And I verify the "Card Issuing Bank" field contains "Test Bank"
  And I verify the "Card Customer Service Phone" field contains "8005550199"
  And I verify the "Sex" field contains "Male"
  And I verify the "Social Security Number" field contains "000-00-0000"
  And I verify the "Driver License Number" field contains "TEST123456"
  And I verify the "Date Of Birth Day" dropdown has "15" selected
  And I verify the "Date Of Birth Month" dropdown has "Jun" selected
  And I verify the "Date Of Birth Year" dropdown has "1995" selected
  And I verify the "Age" field contains "31"
  And I verify the "Birth Place" field contains "Springfield"
  And I verify the "Income" field contains "75000"
  And I verify the "Custom Message" field contains "RoboForm full form filling test"
  And I verify the "Comments" field contains "Testing all available form fields with sample data"
