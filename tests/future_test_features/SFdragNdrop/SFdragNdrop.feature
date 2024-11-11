Feature: Rearrange App launcher icons

  # This feature tests the browser automation capabilities to rearrange icons for Salesforce App Launcher

  Scenario Outline: User rearranges the apps on App Launcher
    login to salesforce
    Given a user is on the Salesforce homepage after the login process
    When the user clicks on the App Luancher icon
    And clicks on View All link
    And drags Community icon to third place
    Then Community app should be in the third column on the first row.
    #spelling mistake on line 7 is intentional