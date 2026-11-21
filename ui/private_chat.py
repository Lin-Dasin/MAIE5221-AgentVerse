"""Use the shared model client for a text-only turn in a private conversation."""

from pathlib import Path

from bus.messages import MessageBus, MessageEvent
from protocol.models import ChatMessage
from runtime.client import DeepSeekClient
from runtime.profiles import load_profiles


async def chat(
    bus: MessageBus,
    conversation: str,
    sender: str,
    agent_id: str,
    text: str,
    profiles_dir: Path,
) -> MessageEvent:
    sender, agent_id = sender.strip(), agent_id.strip()
    if not text.strip():
        raise ValueError("Chat text must not be empty")

    history = bus.private_chat_history(conversation, sender, agent_id)
    profile = load_profiles(profiles_dir).get(agent_id)
    if profile is None or profile.role != "worker":
        raise ValueError(f"Chat requires an existing Worker profile: {agent_id}")

    messages = [ChatMessage(role="system", content=profile.system_prompt)]
    messages.extend(
        ChatMessage(role="user" if event.sender == sender else "assistant", content=event.text)
        for event in history
        if event.kind == "message"
    )
    messages.append(ChatMessage(role="user", content=text))
    client = DeepSeekClient.from_environment()
    try:
        response = await client.complete(messages, [], max_tokens=512)
    finally:
        await client.aclose()
    if response.tool_calls or not response.content.strip():
        raise RuntimeError("The model did not return a usable text reply")

    return bus.record_private_exchange(
        conversation,
        sender,
        agent_id,
        text,
        response.content,
        expected_sequence=history[-1].sequence if history else 0,
    )
