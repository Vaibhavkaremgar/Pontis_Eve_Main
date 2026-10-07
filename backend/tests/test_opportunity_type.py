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


def test_production_false_positive_talent_acquisition_coordinator_is_job():
    assert classify_opportunity_type(
        {"title": "Talent Acquisition Coordinator", "employment_type": "Contract"},
        "This is a 6 month, contract, remote opportunity. 1+ year of professional or internship experience required.",
    ) == "job"


def test_production_false_positive_senior_talent_program_manager_is_job():
    assert classify_opportunity_type(
        {"title": "Sr. Talent Program Manager - Early Careers", "employment_type": "Full-time"},
        "Design and manage internship and early-career programs. Requires 6+ years of experience. Salary $118k-$147k.",
    ) == "job"


def test_graduate_program_with_interns_and_new_grads_function_is_not_automatically_internship():
    assert classify_opportunity_type(
        {
            "title": "AddeGrad Program (2027) - Client Solutions Analyst",
            "structured_data": {"job_board_function": "Interns & New Grads"},
        },
        "An 18-month post-graduate program.",
    ) == "job"


def test_description_explicitly_assigning_candidate_as_intern_is_internship():
    assert classify_opportunity_type(
        {"title": "Client Solutions Analyst"},
        "Join our team as an intern in this 12-week program.",
    ) == "internship"
