Feature: Profile image upload
  
  Scenario: User upload image
    Given a user is on the URL page
    When within File upload via POST (HTTPS) section, the user upload file [image] via upload tool using Choose File selector.
    And the user start https upload
    Then user should get a confirmation of "File successfully uploaded"