Feature: Create Category

    Background: login
        Given login with username and password in the request body as per spec.
        And the response should contain an access token

    Scenario: Successful creation of a category
        Given the API endpoint "/categories" is available
        And the category details are valid with name "New Category + current_timestamp" and description "New Description + current_timestamp"
        When the client makes a POST request with the category details
        Then the response status code should be 200
        And the response should contain the new category information

    Scenario: Creation of a category without name
        Given the API endpoint "/categories" is available
        When the client makes a POST request without the category name
        Then the response status code should be 422
        And the response should contain a validation error message