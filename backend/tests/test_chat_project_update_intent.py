import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import server


DESCRIPTION = "Built REST APIs using Java and Spring Boot with PostgreSQL."


def test_project_description_wins_over_global_skills():
    updates = server._infer_profile_updates_from_message(
        f"Update my Employee Management System project with this description: {DESCRIPTION}"
    )
    assert updates["projects"][0]["title"] == "Employee Management System"
    assert updates["projects"][0]["description"] == DESCRIPTION.rstrip(".")
    assert "skills" not in updates


def test_project_technologies_are_stored_on_project():
    updates = server._infer_profile_updates_from_message(
        "Add Java, Spring Boot and PostgreSQL to my Employee Management System project."
    )
    assert updates["projects"][0]["technologies"] == ["Java", "Spring Boot", "PostgreSQL"]
    assert "skills" not in updates


def test_project_update_preserves_existing_and_merges_technologies():
    existing = [{"title": "Employee Management System", "description": "Old", "technologies": ["Java"], "role": "Developer"}]
    merged = server._merge_projects(existing, [{"title": "Employee Management System", "description": DESCRIPTION, "technologies": ["Spring Boot"]}])
    assert merged == [{"title": "Employee Management System", "description": DESCRIPTION, "technologies": ["Java", "Spring Boot"], "role": "Developer"}]


def test_normal_skills_update_remains_global():
    updates = server._infer_profile_updates_from_message("Add Java and Spring Boot to my skills")
    assert updates["skills"] == ["Java", "Spring Boot"]
    assert "projects" not in updates
