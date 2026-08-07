# Advisories describe the wrong country

**Source:** mcp

check_travel_advisories returns one fixed payload regardless of region - Japan's 90-day visa waiver, Japanese encephalitis, earthquake preparedness - echoing back whatever region label it was given. A request for France yields Japan's entry requirements titled France.
