# Intent fallback invents the whole trip

**Source:** mcp

classify_intent falls back to destination Tokyo, region Japan, days 7, budget 3000 whenever the intent LLM emits unparseable JSON. The pipeline then plans a trip nobody asked for, with no signal to the traveller that their request was not understood.
