# Travel Planner Quality
A travel planning AI system must reliably interpret user intent (booking, modifying, comparing, or asking about trips), route requests to the correct tools and sub-agents, and produce complete, accurate itineraries that respect stated constraints. 
Quality failures include: 
- misclassifying user intent (e.g., treating a booking request as a general question), 
- calling the wrong tool or skipping a required tool (e.g., not checking hotel availability for a booking request)
- returning fabricated details such as invented flight numbers, hotel names, or prices not grounded in tool results 
- ignoring or violating explicit user constraints (budget limits, date ranges, destination preferences) 
- producing incomplete plans that omit required components (flights, accommodation, or transfers) 
- inefficient tool usage such as redundant searches or unnecessary round-trips
- failing to surface important caveats (travel advisories, visa requirements, seasonal closures) when the information is available
- poor multi-turn coherence — losing context, contradicting earlier responses, or requiring the user to repeat information across turns.