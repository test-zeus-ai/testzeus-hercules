Feature: Profile picture

  Scenario: Upload an avatar
    Given I open the page "http://localhost:3000/profile"
    When I upload the file "avatar.png" to the picture field
    Then the page should contain "Picture updated"
