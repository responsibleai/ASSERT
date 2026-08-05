# Hotel rate and availability are invented

**Source:** mcp

`search_hotels` returns nothing for the requested dates, or returns properties without rates. The optimizer supplies a nightly rate and asserts availability anyway. The traveller plans around lodging that may be full or priced very differently, and the total cost of the trip is wrong by the largest single line item after airfare.</description>
<parameter name="additional_context">Same mechanism as the flight-price failure but a separate observable claim type; availability assertions add a second harm beyond pricing, since "rooms are available" is acted on by date rather than by amount.
