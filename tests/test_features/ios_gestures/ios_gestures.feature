Feature: iOS Gesture Testing
  Test various iOS-specific gestures and interactions
  
  Background: 
    Given I am on the iOS app
    And I have accepted all permissions

  @ios_only
  Scenario: Test pinch zoom gestures
    Given I am on the photo viewer screen
    When I perform a pinch out gesture with scale 2.0
    Then the image should be zoomed in
    When I perform a pinch in gesture with scale 0.5
    Then the image should be zoomed out

  @ios_only
  Scenario: Test 3D Touch interactions
    Given I am on the main menu
    When I perform a force touch on the "Share" button with pressure 0.8
    Then I should see the share menu
    And I should feel haptic feedback
    When I select "Copy" from the menu
    Then the content should be copied

  @ios_only
  Scenario: Test double tap interactions
    Given I am viewing an article
    When I double tap on a paragraph
    Then the paragraph should be selected
    When I double tap the zoom control
    Then the content should be zoomed

  @ios_only
  Scenario: Test system alert handling
    Given I open the notification settings
    When a permission alert appears
    Then I should be able to get all alert buttons
    When I click the "Allow" button on the alert
    Then the permission should be granted
    And I should see the notification settings page

  @ios_only
  Scenario: Test haptic feedback
    Given I am on the feedback demo screen
    When I trigger "light" haptic feedback
    Then I should feel a light tap
    When I trigger "heavy" haptic feedback
    Then I should feel a strong tap
    When I trigger "selection" haptic feedback
    Then I should feel a selection feedback