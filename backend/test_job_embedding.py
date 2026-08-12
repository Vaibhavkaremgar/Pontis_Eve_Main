import numpy as np
from app.job_ingestion.embedding_service import build_job_text, generate_job_embedding

sample_job = {
    "title": "Senior Software Engineer",
    "company_name": "Acme Corp",
    "department": "Engineering",
    "location": "New York, NY",
    "employment_type": "Full-Time",
    "salary_range": "$140,000 - $180,000",
    "description": "Design and build scalable backend services using Python and AWS. Collaborate with cross-functional teams to deliver high-quality software.",
    "skills_required": "Python, AWS, PostgreSQL, REST APIs, Docker",
    "experience_level": "Senior",
}

text = build_job_text(sample_job)
embedding = generate_job_embedding(sample_job)
norm = np.linalg.norm(embedding)

print("=== Structured Job Text ===")
print(text)
print("\n=== Embedding Stats ===")
print(f"Length     : {len(embedding)}  (expected 384)")
print(f"First 5    : {embedding[:5]}")
print(f"L2 Norm    : {norm:.6f}  (expected ~1.0)")
