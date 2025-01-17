Feature: End-to-End Pet Store Flow Validation

  Background:
    Given the Pet Store API
    And I have login with valid credentials.

  Scenario Outline: Add a New Pet
    When I send a POST request to "/pet" with the pet details:
      """
      {
        "id": <petId>,
        "name": "<petName>",
        "category": {
          "id": <categoryId>,
          "name": "<categoryName>"
        },
        "status": "<status>"
      }
      """
    Then I should receive a "200 OK" response

    Examples:
      | petId | petName      | categoryId | categoryName | status    |
      | 1     | Bulldog      | 101        | Dogs         | available |
      | 2     | Persian Cat  | 102        | Cats         | pending   |

  Scenario Outline: Update an Existing Pet
    Given a pet exists with ID "<storedPetId>"
    When I send a PUT request to "/pet" with the updated details:
      """
      {
        "id": <storedPetId>,
        "name": "<updatedName>",
        "status": "<updatedStatus>"
      }
      """
    Then I should receive a "200 OK" response, don't do get on pet

    Examples:
      | storedPetId | updatedName  | updatedStatus |
      |-------------|--------------|---------------|
      | 1           | Bulldog Plus | sold          |

  Scenario Outline: Delete a Pet
    Given a pet exists with ID "<storedPetId>"
    When I send a DELETE request to "/pet/<storedPetId>"
    Then I should receive a "200 OK" response
    And the pet with ID "<storedPetId>" should no longer exist

    Examples:
      | storedPetId |
      |-------------|
      | 2           |

  Scenario: Complete Pet Store Management Flow
    Given I add a new pet using the "Add a New Pet" scenario
    And I update the pet details using the "Update an Existing Pet" scenario, for the pet 9223372016900018000
    And I sell the pet by placing an order using the "Place an Order for a Pet" scenario
    When I retrieve the pet by ID using the "Retrieve a Single Pet by ID" scenario for id 9223372016900018000
    Then I delete the pet using the "Delete a Pet" scenario for id 9223372016900018000