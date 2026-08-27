const fs = require('fs');
let c = fs.readFileSync('d:/Eve-main/frontend/src/pages/Dashboard.jsx', 'utf8');

// Patch 1: getVoiceIntakeCenterView - add comment (LF endings in this block)
const p1old = '  if (isInProgress) return "chat";\n  if (isVoiceIntakeCompleteStatus(voiceIntakeResume?.status)) return "swipe";\n  return null;\n}';
const p1new = '  if (isInProgress) return "chat";\n  // Return "swipe" for completed so the effect always drives centerView from backend status\n  if (isVoiceIntakeCompleteStatus(voiceIntakeResume?.status)) return "swipe";\n  return null;\n}';
const c1 = c.replace(p1old, p1new);
console.log('p1 applied:', c1 !== c);
c = c1;

// Patch 2: voiceIntakeCenterView effect guard (CRLF endings)
const p2old = '    if (voiceIntakeCenterView) {\r\n      setCenterView(voiceIntakeCenterView);\r\n    }';
const p2new = '    // Backend is the single source of truth: apply whenever it resolves to a non-null view\r\n    if (voiceIntakeCenterView !== null) {\r\n      setCenterView(voiceIntakeCenterView);\r\n    }';
const c2 = c.replace(p2old, p2new);
console.log('p2 applied:', c2 !== c);
c = c2;

// Patch 3: onComplete - remove local status routing, defer to backend refresh (LF endings)
const p3old = '                const s = loadOnboardingState();\n                const completed = isVoiceIntakeCompleteStatus(result?.status);\n                saveOnboardingState({ ...s, voiceIntakeCompleted: completed });\n                setCenterView(completed ? "swipe" : "chat");\n                setTimeout(() => {\n                  refreshProfile();\n                }, 0);';
const p3new = '                // Refresh from backend - voiceIntakeCenterView effect sets centerView\n                // once the authoritative backend status arrives.\n                refreshProfile().then(() => {\n                  setUserProfile((latest) => {\n                    const backendStatus = latest.voice_intake_resume?.status;\n                    const completed = isVoiceIntakeCompleteStatus(backendStatus);\n                    const s = loadOnboardingState();\n                    saveOnboardingState({ ...s, voiceIntakeCompleted: completed });\n                    return latest;\n                  });\n                });';
const c3 = c.replace(p3old, p3new);
console.log('p3 applied:', c3 !== c);
c = c3;

fs.writeFileSync('d:/Eve-main/frontend/src/pages/Dashboard.jsx', c, 'utf8');
console.log('done, new length:', c.length);
