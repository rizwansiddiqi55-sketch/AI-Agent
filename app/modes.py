"""Quick learning-mode commands from the UI.

Each mode adds a short instruction to the user turn. The system prompt never changes,
so the cached prefix stays valid.
"""

MODES: dict[str, dict[str, str]] = {
    "teach": {"label": "Teach me", "instruction": "Start a structured lesson on the topic I mention (or suggest one). Follow Explain → Demonstrate → Practice → Test → Correct → Review, in small spoken steps."},
    "simple": {"label": "Explain simply", "instruction": "Explain like I'm a beginner, with a real-world analogy."},
    "deep": {"label": "Deep dive", "instruction": "Give an advanced, technically precise explanation, including vendor differences, failure scenarios and verification commands."},
    "quiz": {"label": "Quiz me", "instruction": "Quiz me one question at a time. Wait for my answer, evaluate it, then ask the next."},
    "lab": {"label": "Give me a lab", "instruction": "Create a hands-on lab: objective, topology, prerequisites, configuration, commands, expected output, verification, troubleshooting, challenge task."},
    "interview": {"label": "Interview me", "instruction": "Start a mock interview for the role I mention (default: Senior Network Engineer). One question at a time; after each answer give Score /10, what was good, what to improve, and a better answer."},
    "english": {"label": "Correct my English", "instruction": "Focus on my English. Use the format: What you said / Better / Why, then ask me to repeat the corrected sentence. Log important corrections."},
    "urdu": {"label": "Speak Urdu", "instruction": "From now on, respond primarily in Urdu (keep technical terms in English)."},
    "english_lang": {"label": "Speak English", "instruction": "From now on, respond primarily in English."},
    "translate": {"label": "Translate", "instruction": "Translate what I say naturally between English and Urdu."},
    "revise": {"label": "Revise", "instruction": "Review previous material using my saved notes and progress."},
    "test": {"label": "Test me", "instruction": "Create a short assessment on my current topic, one question at a time, then update my progress based on results."},
    "homework": {"label": "Homework", "instruction": "Give me practical homework exercises for my current topic and save them as a note."},
    "continue": {"label": "Continue", "instruction": "Continue my current course from the last saved lesson position."},
    "plan": {"label": "Plan my day", "instruction": "Create today's practical study plan based on my progress and goals, then ask if I'm ready for the first exercise."},
}


def mode_instruction(mode: str | None) -> str | None:
    if not mode:
        return None
    entry = MODES.get(mode)
    return entry["instruction"] if entry else None
