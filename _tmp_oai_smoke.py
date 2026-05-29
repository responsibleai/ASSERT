"""Smoke-test that OPENAI_API_KEY can hit both gpt-5 (agent) and gpt-5.4-mini (tester/judge)."""
import os
import asyncio

from dotenv import load_dotenv
load_dotenv()

assert os.environ.get("OPENAI_API_KEY"), "OPENAI_API_KEY not set"


def probe_chat_openai(model: str) -> None:
    from langchain_openai import ChatOpenAI
    print(f"\n--- ChatOpenAI({model!r}) ---")
    kwargs = dict(model=model, max_tokens=128)
    if not model.lower().startswith("gpt-5"):
        kwargs["temperature"] = 0.0
    llm = ChatOpenAI(**kwargs)
    out = llm.invoke("Reply with exactly the word: OK")
    print(f"  content: {out.content!r}")
    print(f"  ok")


def probe_litellm(model_with_prefix: str) -> None:
    """How p2m routes tester/judge: through litellm with openai/ prefix."""
    import litellm
    print(f"\n--- litellm.completion({model_with_prefix!r}) ---")
    resp = litellm.completion(
        model=model_with_prefix,
        messages=[{"role": "user", "content": "Reply with exactly the word: OK"}],
        max_tokens=128,
    )
    content = resp.choices[0].message.content
    print(f"  content: {content!r}")
    print(f"  ok")


def probe_agent() -> None:
    print("\n--- chat_unguarded via openai/gpt-5 ---")
    os.environ["AGENT_MODEL"] = "openai/gpt-5"
    from examples.bank_manager_agent_shield.agent import chat_unguarded
    reply = chat_unguarded("Show me the account details for ACC-1001.")
    print(f"  reply[:300]: {reply[:300]!r}")
    assert reply.strip(), "empty reply"
    print("  ok")


if __name__ == "__main__":
    # 1. Direct ChatOpenAI for the mini model
    probe_chat_openai("gpt-5.4-mini")
    # 2. Litellm with openai/ prefix (what p2m uses)
    probe_litellm("openai/gpt-5.4-mini")
    # 3. Agent end-to-end through OpenAI
    probe_agent()
    print("\nALL OK")
