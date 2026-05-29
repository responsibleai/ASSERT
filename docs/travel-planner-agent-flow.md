# Travel Planner Agent Dynamic Execution Flow

This page visualizes the flagship customer-preview example:

- `examples\travel_planner_langgraph\agent.py`
- `examples\travel_planner_langgraph\auto_trace.py`
- `examples\travel_planner_langgraph\eval_config.yaml`

`auto_trace.py` registers Phoenix/OpenInference auto-instrumentation and imports `chat_sync`. The LangGraph agent itself lives in `agent.py`.

## Runtime graph

```mermaid
flowchart TD
  subgraph ASSERT["ASSERT inference loop"]
    Seed["generated test case"]
    Tester["tester LLM<br/>next user turn"]
    Runtime["CallableSession.run_turn<br/>calls target callable"]
    InferenceSet["inference_set.jsonl<br/>conversation or agent actions + trace refs"]
    Judge["judge stage<br/>scores against spec"]

    Seed --> Tester
    Tester -->|"user turn"| Runtime
    Runtime -->|"target response"| Tester
    Tester --> InferenceSet
    Runtime --> InferenceSet
    InferenceSet --> Judge
  end

  subgraph AutoTrace["examples.travel_planner_langgraph.auto_trace"]
    Phoenix["phoenix.otel.register(auto_instrument=True)"]
    ChatSync["chat_sync(message)"]
    Phoenix -. "auto-instruments LangChain / LangGraph / OpenAI calls" .-> ChatSync
  end

  Runtime --> ChatSync

  subgraph Agent["LangGraph travel planner agent"]
    Start((START))
    Intent["intent_classifier<br/>extract intent, destination, budget"]
    HasDestination{"destination present?"}
    Research["research<br/>LLM bound to travel tools"]
    HasToolCalls{"tool_calls emitted?"}
    ToolNode["ToolNode executes tools"]

    Flights["search_flights"]
    Hotels["search_hotels"]
    Weather["check_weather"]
    Advisories["check_travel_advisories"]
    BudgetTool["validate_budget"]

    ToolResults["tool results appended<br/>as ToolMessages"]
    Safety["safety_checker"]
    Budget["budget_validator"]
    Itinerary["itinerary_optimizer"]
    GoodAnswer{"final answer<br/>content length > 50?"}
    Clarify["clarification"]
    End((END))

    Start --> Intent
    Intent --> HasDestination
    HasDestination -->|"yes"| Research
    HasDestination -->|"no"| Clarify
    Research --> HasToolCalls
    HasToolCalls -->|"yes"| ToolNode
    HasToolCalls -->|"no"| ToolResults
    ToolNode --> Flights
    ToolNode --> Hotels
    ToolNode --> Weather
    ToolNode --> Advisories
    ToolNode --> BudgetTool
    Flights --> ToolResults
    Hotels --> ToolResults
    Weather --> ToolResults
    Advisories --> ToolResults
    BudgetTool --> ToolResults
    ToolResults --> Safety
    Safety --> Budget
    Budget --> Itinerary
    Itinerary --> GoodAnswer
    GoodAnswer -->|"yes"| End
    GoodAnswer -->|"no"| Clarify
    Clarify --> End
  end

  ChatSync --> Start
  End -->|"last AIMessage.content"| Runtime
```

## Runtime sequence

```mermaid
sequenceDiagram
  participant Seed as Test Case
  participant Tester as Tester LLM
  participant Runtime as CallableSession
  participant Trace as Phoenix OTel
  participant Graph as LangGraph Agent
  participant LLM as AzureChatOpenAI
  participant Tools as ToolNode + travel tools
  participant Judge as Judge

  Seed->>Tester: initialize objective

  loop up to inference.max_turns
    Tester->>Tester: generate next user message
    Tester->>Runtime: user turn
    Runtime->>Graph: chat_sync(message)
    Graph->>LLM: intent_classifier
    LLM-->>Graph: intent, destination, budget

    alt destination missing
      Graph->>LLM: clarification
      LLM-->>Graph: follow-up question
    else destination present
      Graph->>LLM: research with bound tools
      LLM-->>Graph: AIMessage with tool_calls
      opt tool_calls present
        Graph->>Tools: execute requested tools
        Tools-->>Graph: ToolMessages
        Trace-->>Trace: capture spans
      end
      Graph->>LLM: safety_checker
      LLM-->>Graph: safety review
      Graph->>LLM: budget_validator
      LLM-->>Graph: budget verdict
      Graph->>LLM: itinerary_optimizer
      LLM-->>Graph: final itinerary
    end

    Graph-->>Runtime: final AIMessage.content
    Runtime-->>Tester: target response
    Runtime-->>Trace: captured spans
  end

  Runtime->>Judge: transcript + trace artifacts
  Judge->>Judge: score against spec
```

## Caveat

`chat_sync(message: str)` does not accept `history`, so ASSERT maintains the outer multi-turn transcript while each target invocation is a fresh graph run from the agent's perspective.
