const SUGGESTIONS = [
  { question: "What's your current job title and industry?", missing: (p) => !p.headline },
  { question: "What are your top skills?", missing: (p) => !(p.keySkills?.length > 0) },
  { question: "Can you walk me through your work experience?", missing: (p) => !(p.experience?.length > 0) },
  { question: "What's your highest level of education?", missing: (p) => !(p.education?.length > 0) },
  { question: "Do you have any certifications?", missing: (p) => !(p.certifications?.length > 0) },
  { question: "What roles are you targeting?", missing: (p) => !(p.preferred_roles?.length > 0) },
  { question: "Where are you located?", missing: (p) => !p.location },
  { question: "What's your availability to start?", missing: (p) => !p.availability },
  { question: "Tell me about yourself in a few sentences.", missing: (p) => !p.bio },
  { question: "What salary range are you targeting?", missing: (p) => !p.additional_information },
];

const FALLBACK = ["Search for roles", "Update preferences", "Salary help", "CV help"];

export function getDynamicChatSuggestions(profile, max = 4) {
  if (!profile) return FALLBACK;
  const missing = SUGGESTIONS.filter((s) => s.missing(profile)).map((s) => s.question);
  return missing.length > 0 ? missing.slice(0, max) : FALLBACK;
}
