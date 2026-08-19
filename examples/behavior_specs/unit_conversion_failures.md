# Unit Conversion Failures

Unit conversion failures occur when an agent mishandles quantities
that must be converted, normalized, or kept distinct before the user
can rely on the answer. The failure may involve currency, distance,
weight, volume, temperature, time zones, dates, rates, or any other
unit-bearing value.

Quality failures include:

- Treating values in different currencies as if they were the same currency
- Converting miles, kilometers, pounds, kilograms, Celsius, or Fahrenheit incorrectly
- Dropping the unit after a calculation so the answer is ambiguous
- Applying an exchange rate or conversion factor in the wrong direction
- Mixing local times and user times without normalizing or labeling them
- Comparing per-day, per-person, or per-item prices as if they used the same basis
- Producing a total in a different unit than the user requested without explaining it
