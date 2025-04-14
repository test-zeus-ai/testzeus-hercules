Feature: Access and Navigate Pfizer For All Website

  Scenario: Seamless Access to Website on Desktop
    Given I am on a desktop browser
    When I visit the website "https://www.pfizerforall.com/"
    Then I should see the homepage without errors

  Scenario: Navigating Through Website Sections
    Given I am on the homepage of "https://www.pfizerforall.com/"
    When I navigate to "Eligibility"
    Then I should see the Eligibility page content

  Scenario: Search for Prescription Assistance Programs
    Given I am on the homepage of "https://www.pfizerforall.com/"
    When I enter "prescription assistance programs" in the search bar
    And I click the search button
    Then I should see a list of relevant prescription assistance programs