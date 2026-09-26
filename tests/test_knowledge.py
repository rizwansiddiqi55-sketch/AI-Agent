import json

import pytest

from app import knowledge
from app.tools import ToolInputError, execute_tool


def test_library_files_are_valid():
    topics = knowledge.load_topics()
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids)) and len(topics) >= 20
    for t in topics:
        assert t["subject"] in {"Networking", "Cybersecurity", "Python", "AI", "English"}
        assert t["key_points"] and t["qa"]
        for item in t["qa"]:
            assert item["q"].strip() and item["a"].strip()
            assert item["level"] in {"Beginner", "Intermediate", "Advanced"}


@pytest.mark.parametrize("query,expected_topic", [
    ("OSPF stuck in ExStart", "OSPF"),
    ("bgp neighbor stuck active", "BGP"),
    ("printer without 802.1x", "NAC, 802.1X and Cisco ISE"),
    ("ipsec phase 2 fails", "VPNs and IPsec"),
    ("netmiko show ip interface brief", "Network Automation (Netmiko, NAPALM, Nornir, Ansible, APIs)"),
    ("how does rag work", "RAG (Retrieval-Augmented Generation)"),
    ("since or for grammar", "Common English Corrections for Technical Professionals"),
])
def test_search_finds_the_right_topic(query, expected_topic):
    results = knowledge.search(query)
    assert results and results[0]["topic"] == expected_topic


def test_search_subject_filter_and_empty_query():
    assert all(r["subject"] == "AI" for r in knowledge.search("protocol", subject="AI"))
    assert knowledge.search("the what is") == []


def test_practice_questions_are_reproducible_and_bounded():
    a = knowledge.practice_questions("bgp", 3, seed=7)
    b = knowledge.practice_questions("bgp", 3, seed=7)
    assert a == b and a["topic"] == "BGP" and len(a["questions"]) == 3
    many = knowledge.practice_questions("fhrp hsrp", 50)
    assert len(many["questions"]) == 3  # capped at the questions available
    adv = knowledge.practice_questions("ospf", 5, level="Advanced")
    assert all(q["level"] == "Advanced" for q in adv["questions"])


def test_search_knowledge_tool(memory):
    out = json.loads(execute_tool(memory, "search_knowledge", {"query": "OSPF MTU mismatch"}))
    assert out["results"] and any("model_answer" in r or "key_points" in r for r in out["results"])
    none = json.loads(execute_tool(memory, "search_knowledge", {"query": "quantum knitting"}))
    assert none["results"] == [] and none["topics"]


def test_practice_questions_tool(memory):
    out = json.loads(execute_tool(memory, "get_practice_questions", {"topic": "IPsec", "count": 2}))
    assert out["topic"] == "VPNs and IPsec" and len(out["questions"]) == 2
    assert "model_answer" in out["questions"][0] and "instructions" in out


@pytest.mark.parametrize("data", [
    {"topic": "bgp", "count": "2"},
    {"topic": "bgp", "count": 0},
    {"topic": "bgp", "count": 9},
    {"topic": "bgp", "count": True},
    {"topic": "bgp", "level": "Expert"},
])
def test_practice_questions_input_validation(memory, data):
    with pytest.raises(ToolInputError):
        execute_tool(memory, "get_practice_questions", data)
