import logging
from typing import Literal
from typing_extensions import Annotated, TypedDict, Optional

from langgraph.constants import TAG_NOSTREAM
from langgraph.graph import StateGraph, add_messages
from langgraph.types import Command
from langchain_openai.chat_models import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt
from langgraph.types import StreamWriter

logger = logging.getLogger(__name__)


class TypedLivekit:
    writer: StreamWriter

    def __init__(self, writer: StreamWriter):
        self.writer = writer

    def say(self, content: str):
        self.writer({"type": "say", "data": {"content": content}})

    def flush(self):
        self.writer({"type": "flush", "data": None})


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    title: Optional[str]
    content: Optional[str]


async def human(state: AgentState, writer: StreamWriter) -> AgentState:
    livekit = TypedLivekit(writer)
    livekit.say("This is a human node")

    title, title_msgs = interrupt("What is the title of the article?")
    content, content_msgs = interrupt("What is the content of the article?")

    logger.info(f"human: {title} {content}")
    return {"title": title, "content": content, "messages": title_msgs + content_msgs}


async def weather(state: AgentState) -> AgentState:
    response = await ChatOpenAI(model="gpt-4o-mini").ainvoke(
        [HumanMessage(content="Tell me a random weather fact")]
    )

    logger.info(f"weather: {response}")
    return {"messages": response}


async def other(state: AgentState) -> AgentState:
    response = await ChatOpenAI(model="gpt-4o-mini").ainvoke(
        [HumanMessage(content=state["messages"][-1].content)]
    )

    logger.info(f"other: {response}")
    return {"messages": response}


async def supervisor(
    state: AgentState, writer: StreamWriter
) -> Command[Literal["weather", "other"]]:
    livekit = TypedLivekit(writer)

    class RouterOutput(TypedDict):
        next_step: Annotated[
            Literal["weather", "other"], ..., "Classify the user request"
        ]

    response = await (
        ChatOpenAI(model="gpt-4o-mini")
        .with_structured_output(RouterOutput)
        .with_config(tags=[TAG_NOSTREAM])
    ).ainvoke([HumanMessage(content=state["messages"][-1].content)])

    # Send a flush event to send directly to TTS
    livekit.flush()
    logger.info(f"supervisor: {response}")

    if response["next_step"] == "weather":
        return Command(goto="weather")
    else:
        return Command(goto="other")


builder = StateGraph(AgentState)
builder.add_node(human)
builder.add_node(supervisor)
builder.add_node(weather)
builder.add_node(other)
builder.set_entry_point("human")
builder.add_edge("human", "supervisor")

graph = builder.compile()
