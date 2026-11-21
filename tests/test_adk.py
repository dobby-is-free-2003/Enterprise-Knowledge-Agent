"""ADK runtime tests with a scripted model: no network, but the real ADK runner, agents, tools and session state."""
import asyncio

import pytest
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from eka.adk_app import AdkRuntime


class ScriptedLlm(BaseLlm):
    """Plays the model. `route` = specialist to hand off to; `tool_calls` = what the specialist calls; `final` = reply text."""
    model: str = "scripted"
    route: str = "docs_agent"
    tool_calls: list = []
    final: str = "done"
    explode: bool = False

    @classmethod
    def supported_models(cls):
        return ["scripted"]

    async def generate_content_async(self, llm_request, stream=False):
        if self.explode:
            raise RuntimeError("model down")
        names = set(llm_request.tools_dict)
        done = {p.function_response.name for c in llm_request.contents for p in (c.parts or []) if p.function_response}
        resp = {p.function_response.name: p.function_response.response for c in llm_request.contents for p in (c.parts or []) if p.function_response}
        if "transfer_to_agent" in names and not (names & {"search_docs", "query_database", "propose_email_draft"}):
            if "transfer_to_agent" not in done:
                part = types.Part(function_call=types.FunctionCall(name="transfer_to_agent", args={"agent_name": self.route}))
            else:
                part = types.Part(text=self.final)
        else:
            pending = [c for c in self.tool_calls if c[0] not in done]
            if pending:
                part = types.Part(function_call=types.FunctionCall(name=pending[0][0], args=pending[0][1]))
            else:
                text = self.final
                if "{P1}" in text:  # quote the first retrieved passage, as a faithful model would
                    ps = (resp.get("search_docs") or {}).get("passages") or [{"text": "nothing"}]
                    text = text.replace("{P1}", ps[0]["text"].split("\n")[-1][:200])
                part = types.Part(text=text)
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def ask(deps, user, question, **model_kw):
    rt = AdkRuntime(deps, model=ScriptedLlm(**model_kw))
    return asyncio.run(rt.ask(user, question))


def test_docs_answer_is_grounded_and_cited(deps, U):
    r = ask(deps, U("priya.sharma"), "annual leave?", route="docs_agent",
            tool_calls=[("search_docs", {"query": "annual leave days"})],
            final="Full-time employees receive 24 days of paid annual leave per calendar year. [P1]")
    assert r.status == "answered" and "24 days" in r.answer and "[P1]" not in r.answer
    assert r.citations[0]["file_id"] == "hr-leave-policy"


def test_hallucinated_answer_with_a_citation_is_refused(deps, U):
    r = ask(deps, U("priya.sharma"), "annual leave?", route="docs_agent",
            tool_calls=[("search_docs", {"query": "annual leave days"})],
            final="Everyone gets unlimited leave plus a company car. [P1]")
    assert r.status == "refused" and any(f.startswith("grounding_failed") for f in r.flags)


def test_answer_without_any_citation_is_refused(deps, U):
    r = ask(deps, U("priya.sharma"), "annual leave?", route="docs_agent", tool_calls=[], final="24 days.")
    assert r.status == "refused"


def test_identity_comes_from_session_not_from_the_model(deps, U):
    """Even a model that searches for restricted content as hard as it can only sees what the signed-in user may read."""
    r = ask(deps, U("priya.sharma"), "salary band?", route="docs_agent",
            tool_calls=[("search_docs", {"query": "salary band Staff Engineer"})],
            final="Staff Engineer salary band is 35 to 55 lakh. [P1]")
    assert r.status == "refused" and "35 to 55" not in r.answer and "hr-salary-bands" not in r.retrieved_files
    r2 = ask(deps, U("rahul.verma"), "salary band?", route="docs_agent",
             tool_calls=[("search_docs", {"query": "salary band Staff Engineer"})], final="{P1} [P1]")
    assert r2.status == "answered" and "hr-salary-bands" in r2.retrieved_files


def test_sql_runs_through_the_guarded_tool(deps, U):
    q = "SELECT COUNT(*) AS n FROM customers WHERE region='north'"
    r = ask(deps, U("anita.desai"), "how many north customers", route="data_agent", tool_calls=[("query_database", {"sql": q})])
    truth = deps.sql.run(q, U("anita.desai")).rows[0][0]
    assert r.status == "answered" and r.rows == [[truth]] and r.intent == "data"


def test_sql_for_user_without_access_is_blocked_even_if_model_tries(deps, U):
    r = ask(deps, U("priya.sharma"), "customers?", route="data_agent",
            tool_calls=[("query_database", {"sql": "SELECT * FROM customers"})])
    assert r.status == "blocked" and r.rows == []


def test_hostile_sql_from_the_model_is_blocked(deps, U):
    r = ask(deps, U("anita.desai"), "x", route="data_agent", tool_calls=[("query_database", {"sql": "DROP TABLE customers"})])
    assert r.status == "blocked"
    assert deps.sql.run("SELECT COUNT(*) FROM customers", U("anita.desai")).rows == [[40]]


def test_email_draft_needs_approval(deps, U):
    r = ask(deps, U("priya.sharma"), "draft", route="actions_agent",
            tool_calls=[("propose_email_draft", {"to": ["rahul.verma@northwind.example"], "subject": "Hi", "body": "Hello"})])
    assert r.status == "pending_approval" and r.intent == "action"
    assert list(deps.settings.outbox_dir.glob("*.json")) == []


def test_external_recipient_never_becomes_a_pending_approval(deps, U):
    r = ask(deps, U("priya.sharma"), "draft", route="actions_agent",
            tool_calls=[("propose_email_draft", {"to": ["x@evil.example"], "subject": "Hi", "body": "Hello"})])
    assert r.status != "pending_approval" and deps.approvals.pending_for(U("priya.sharma")) == []


def test_model_outage_degrades_to_a_refusal_and_is_audited(deps, U):
    me = U("priya.sharma")
    r = ask(deps, me, "annual leave?", explode=True)
    assert r.status == "refused" and "runtime_error:RuntimeError" in r.flags
    assert deps.audit.recent(5, me.email)[0]["answer_status"] == "refused"
