@env:pre-seeded-restful-booker @kind:api
Feature: RESTful Booker booking management

Scenario: Create a booking and verify the booking details
  When I send a POST request to "/booking" with the header "Content-Type: application/json" and the JSON body:
    """
    {
      "firstname": "Alex",
      "lastname": "Morgan",
      "totalprice": 500,
      "depositpaid": true,
      "bookingdates": {
        "checkin": "2026-12-01",
        "checkout": "2026-12-10"
      },
      "additionalneeds": "Breakfast"
    }
    """
  Then the response status code should be 200
  And the response JSON "$.bookingid" should not be empty
  And the response JSON "$.booking.firstname" should equal "Alex"
  And the response JSON "$.booking.lastname" should equal "Morgan"
  And the response JSON "$.booking.totalprice" should equal 500
  And the response JSON "$.booking.depositpaid" should equal true
  And the response JSON "$.booking.bookingdates.checkin" should equal "2026-12-01"
  And the response JSON "$.booking.bookingdates.checkout" should equal "2026-12-10"
  And the response JSON "$.booking.additionalneeds" should equal "Breakfast"
  When I save the response JSON "$.bookingid" as "bookingId"
  And I send a GET request to "/booking/{{bookingId}}"
  Then the response status code should be 200
  And the response JSON "$.firstname" should equal "Alex"
  And the response JSON "$.lastname" should equal "Morgan"
  And the response JSON "$.totalprice" should equal 500
  And the response JSON "$.depositpaid" should equal true
  And the response JSON "$.bookingdates.checkin" should equal "2026-12-01"
  And the response JSON "$.bookingdates.checkout" should equal "2026-12-10"
  And the response JSON "$.additionalneeds" should equal "Breakfast"
