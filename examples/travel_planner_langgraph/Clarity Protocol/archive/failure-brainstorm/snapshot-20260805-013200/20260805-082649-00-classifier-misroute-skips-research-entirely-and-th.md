# Classifier misroute skips research entirely and the whole plan is invented

**Source:** mcp

`intent_classifier` mislabels a genuine booking request, so the graph never reaches `research` and no tool is called at all. `itinerary_optimizer` still runs and produces a full itinerary — flights, hotels, weather, total — sourced entirely from model recall. Every downstream fabrication mode fires simultaneously, and nothing in the output signals that zero retrieval occurred.</description>
<parameter name="additional_context">This is the compounding case: routing is a single low-temperature classification with no verification, and a single misroute removes the entire evidentiary basis for the answer while leaving output quality superficially unchanged.
