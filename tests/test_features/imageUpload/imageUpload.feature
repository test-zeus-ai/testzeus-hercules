Feature: Profile image upload
  
  Scenario: User creates a new calendar item
    Given a user is on the OrangeHRM home page
    And the user logs in with "Admin" as Username and "admin123" as Password
    And the user clicks on "My Info" on the left navigation panel
    And the user clicks on Attachment add button
    And the user clicks on browse to upload the image
    And the user uploads a Attachment image
    And the user clicks on Save button
    Then the page should get a toast notification of successfully saved.
    And the page should get the file in the attachment list on the page.