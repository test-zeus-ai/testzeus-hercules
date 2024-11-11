Feature: Gantt chart validation feature
  #This feature tests Hercules against Gantt charts on the Salesforce platform
  Scenario: User creates a new calendar item
    Given a user is on the Salesforce login
    When the user logs in 
    And the user navigates to the calendar tab
    And the user toggles the Day view
    And the user double clicks on an empty slot on the current date
    And the user enters name as "Hercules Testing Event"
    And the user enters Start time as 5.30PM
    And the user clicks Save button
    And the user toggles the view to Agenda
    Then the user should be able to see the "Hercules Testing Event" at 5.30PM slot
      
 Scenario: User creates a new scheduler item
    Given a user is on the Salesforce login
    When the user logs in     
    And the user navigates to Bryntum scheduler tab
    And the user double clicks on an empty space in the "Paint" lane
    And the user enters Name as "Car paint"
    And the user sets the Start Date as "10 December 2017"
    And the user clicks Save button
    Then the task of "Car paint" should be visible on the scheduler screen
      
    
 Scenario: User extends the timeline for the car paint task
    Given a user is on the Salesforce login
    When the user logs in     
    And the user navigates to Bryntum scheduler tab
    And the user double clicks on the task "ABC103" and notes down the end time of the task
    And the user extends the time of task "ABC103" by dragging its right handle by 1 hour.
    And the user doubkle clicks the task "ABC103"
    Then the end time of "ABC103" should be extended by 1 hour from previous value