import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.job_ingestion.normalize import classify_opportunity_type


def test_internship_is_detected_from_title_or_employment_type():
    assert classify_opportunity_type({"title": "Backend Engineering Intern"}, "Build APIs") == "internship"
    assert classify_opportunity_type({"title": "Backend Engineer", "employment_type": "Internship"}, "Build APIs") == "internship"


def test_normal_job_with_intern_word_in_description_stays_job():
    assert classify_opportunity_type(
        {"title": "Backend Engineer", "employment_type": "Full-time"},
        "Build internal tools and mentor interns.",
    ) == "job"
