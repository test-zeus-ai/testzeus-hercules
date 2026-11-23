@env:pre-seeded-restful-booker @kind:api
Feature: RESTful Booker authentication

Scenario: Generate an authentication token using valid credentials
  When I send a POST request to "/auth" with the header "Content-Type: application/json" and the JSON body:
    """
    {
      "username": "admin",
      "password": "password123"
    }
    """
  Then the response status code should be 200
  And the response JSON "$.token" should not be empty
  When I save the response JSON "$.token" as "authToken"
