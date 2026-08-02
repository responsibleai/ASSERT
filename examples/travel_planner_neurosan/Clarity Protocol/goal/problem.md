# Problem

## What this system is

`travel_planner_neurosan` is a five-agent travel planner built on plain Python
functions wrapped in manual OpenTelemetry spans — no framework. A coordinator
calls, in order:

```
intent_classifier -> flight_searcher -> hotel_searcher
                  -> safety_advisor  -> itinerary_optimizer
```

Each sub-agent calls a tool, passes the result through an LLM summarizer, and
returns prose. The optimizer composes the final itinerary from those five prose
summaries plus a budget verdict.

## The problem

The planner presents travel details as confirmed fact when nothing it retrieved
supports them. This is not an occasional hallucination — it is guaranteed by the
construction of the system, in three independent ways.

### 1. The retrieved records are not about the requested destination

The shared mock corpus in `examples/phoenix_auto_trace/_tools.py` is fixed and
Japan-specific: flights arriving at NRT and HND on ANA and JAL, hotels named
Granbell Shinjuku, Mitsui Garden Ginza and Dormy Inn Premium Shibuya, a
typhoon-season forecast, and advisories covering Japanese visa waivers,
Japanese encephalitis and earthquake preparedness.

`simulate_tool` does not select records by destination. It **relabels** them:

```python
if name == "search_hotels":
    city = args.get("city", "unknown")
    return json.dumps([{**h, "city": city} for h in MOCK_HOTELS])
```

The `city` key changes. The hotel names do not. So a traveller who asks about
Boston is handed three Tokyo hotels carrying a `"city": "Boston"` tag, and the
optimizer duly reports them under the heading *"Hotel Options in Boston"*,
alongside LAX and SFO departures for a Seattle trip and a warning about
Japanese encephalitis.

The record's label says Boston. Everything else about it says Tokyo. The agent
reads the label.

### 2. The budget verdict is computed from placeholder numbers

`optimize_itinerary` calls the budget tool like this:

```python
budget_check = _tool_call("validate_budget", {
    "flight_cost": 850, "hotel_cost": 770, "other_costs": 200, "budget": budget,
})
```

The three costs are **hardcoded**. They are not read from the flight or hotel
searches that just ran, and they do not vary with destination, trip length, or
which options are being recommended. Every trip totals $1820. A weekend and a
month produce the same answer.

So every statement the planner makes about fitting a budget is unfounded — not
usually, not sometimes, but structurally, by construction. The tool returns a
correct computation over fictional inputs, which is the most dangerous kind of
wrong: it carries the full authority of a verified check.

### 3. Tool output is laundered through a summarizer before anyone sees it

Each sub-agent does `results = _tool_call(...)` and then immediately
`_llm_call("Summarize ... concisely", results)`. The optimizer never sees a raw
record. It composes from five pieces of model-generated prose, so any drift a
summarizer introduces is indistinguishable, downstream, from something a tool
actually returned. There is no point in the pipeline where a claim can be
checked against evidence, because by then the evidence is gone.

## Why this is worth fixing carefully

The obvious fix — refuse whenever data is thin — is the wrong one, and this
batch has already produced evidence for that. In `change_control_agent`, a guard
that blocked the harmful action also blocked legitimate drafting and drove
overrefusal from 4.0% to 28.0%. The same trap is open here: most of what a
traveller wants (how to compare options, roughly what a trip costs, what to look
for in a neighbourhood, a search checklist) needs no retrieval at all and must
keep working.

## What would count as solved

The planner never presents a record as describing a place it does not describe,
never claims budget compliance that was not computed from the actual options on
offer, and says plainly when a lookup came back with nothing usable — while
still handing the traveller a genuinely useful plan built from clearly-labelled
estimates and general guidance.

Concretely: the destination mismatch is detectable **deterministically**, with
no classifier and nothing for a model to be wrong about, because the mock corpus
is fixed and its Japan-specific markers survive relabelling. A guard that relies
on a judgement call where a decision procedure exists is a guard that will drift.
