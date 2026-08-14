import React from "react";
import axios from "axios";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Toaster, toast } from "sonner";
import { Bell } from "lucide-react";

import Sidebar from "../components/Sidebar";
import ChatHub from "../components/ChatHub";
import LivingProfile from "../components/LivingProfile";
import SwipeJobDeck from "../components/SwipeJobCard";
import {
  MOCK_RECENT_ACTIVITY,
  QUICK_ACTIONS,
} from "../mock";
import { loadOnboardingState, saveOnboardingState, clearOnboardingState } from "../lib/onboardingStorage";
import { useNavigate, useSearchParams } from "react-router-dom";
import VoiceIntake from "../components/onboarding/VoiceIntake";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// voiceCompleted: whether the candidate finished voice intake
// Resume alone contributes at most 20%; voice intake drives the rest.
function computeStrength(p, voiceCompleted = false) {
  if (!voiceCompleted) {
    // Resume-only score: 10–20% based on how much resume data was parsed
    const fields = [p.name, p.email, p.headline, p.location];
    const filled = fields.filter((f) => typeof f === "string" && f.trim() !== "").length;
    const hasExp = Array.isArray(p.experience) && p.experience.length > 0 ? 1 : 0;
    const hasSkills = Array.isArray(p.keySkills) && p.keySkills.length > 0 ? 1 : 0;
    const raw = filled + hasExp + hasSkills;
    const percent = Math.round(10 + (raw / 6) * 10); // 10–20%
    return { strengthPercent: Math.min(percent, 20), strength: "Building" };
  }
  // Voice-enriched score
  const fields = [p.name, p.email, p.phone, p.headline, p.location, p.bio];
  const filled = fields.filter((f) => typeof f === "string" && f.trim() !== "").length;
  const hasExp = Array.isArray(p.experience) && p.experience.length > 0 ? 1 : 0;
  const hasEdu = Array.isArray(p.education) && p.education.length > 0 ? 1 : 0;
  const hasSkills = Array.isArray(p.keySkills) && p.keySkills.length > 0 ? 1 : 0;
  const hasAvail = p.availability ? 1 : 0;
  const hasRoles = Array.isArray(p.preferred_roles) && p.preferred_roles.length > 0 ? 1 : 0;
  const total = filled + hasExp + hasEdu + hasSkills + hasAvail + hasRoles;
  const percent = Math.round(20 + (total / 11) * 80); // 20–100%
  const label = percent >= 75 ? "Strong" : percent >= 50 ? "Developing" : "Building";
  return { strengthPercent: Math.min(percent, 100), strength: label };
}

function buildFallbackProfile(isOpenToMatches = true) {
  return {
    avatar: null,
    isOpenToMatches,
    strength: "Building",
    strengthPercent: 0,
    name: "",
    email: "",
    phone: "",
    headline: "",
    location: "",
    bio: "",
    experience: [],
    education: [],
    keySkills: [],
    experience_years: null,
    availability: "",
    preferred_roles: [],
    certifications: [],
    additional_information: "",
  };
}

function applyStrength(profile, voiceCompleted = false) {
  return { ...profile, ...computeStrength(profile, voiceCompleted) };
}

function ResizeHandle({ testId, subtle = false }) {
  return (
    <PanelResizeHandle
      data-testid={testId}
      className="group relative w-[6px] shrink-0 bg-transparent"
    >
      <div
        className={`absolute inset-y-0 left-1/2 -translate-x-1/2 w-px transition-colors ${
          subtle
            ? "bg-transparent group-hover:bg-black/[0.06] group-data-[resize-handle-active]:bg-black/[0.14]"
            : "bg-black/[0.05] group-hover:bg-black/[0.15] group-data-[resize-handle-active]:bg-black/[0.28]"
        }`}
      />
    </PanelResizeHandle>
  );
}

function Dashboard() {
  const navigate = useNavigate();
  const stored = React.useMemo(() => loadOnboardingState(), []);
  const [candidateId, setCandidateId] = React.useState(() => loadOnboardingState().candidateId ?? null);
  const isOpenToMatches = stored.isOpenToMatches ?? true;
  const voiceCompleted = stored.voiceIntakeCompleted ?? false;

  const [showWeakProfilePopup, setShowWeakProfilePopup] = React.useState(false);

  const [activeTab, setActiveTab] = React.useState(() => {
    if (stored.newlyOnboarded) {
      saveOnboardingState({ ...stored, newlyOnboarded: false });
      return "profile";
    }
    return "new-jobs";
  });

  const [userProfile, setUserProfile] = React.useState(() =>
    applyStrength(buildFallbackProfile(isOpenToMatches), voiceCompleted)
  );

  // Load real profile from PostgreSQL on mount
  React.useEffect(() => {
    if (!candidateId) return;
    console.log("Current candidateId:", candidateId);
    axios
      .get(`${API}/candidate/${candidateId}/profile`)
      .then((res) => {
        const data = res.data;
        setUserProfile(applyStrength({
          avatar: null,
          isOpenToMatches,
          name: data.name ?? "",
          email: data.email ?? "",
          phone: data.phone ?? "",
          headline: data.headline ?? "",
          location: data.location ?? "",
          bio: data.bio ?? "",
          experience: data.experience ?? [],
          education: data.education ?? [],
          keySkills: data.keySkills ?? [],
          experience_years: data.experience_years ?? null,
          availability: data.availability ?? "",
          preferred_roles: data.preferred_roles ?? [],
          certifications: data.certifications ?? [],
          additional_information: data.additional_information ?? "",
        }, voiceCompleted));
      })
      .catch(() => {
        // Fall back to onboarding parsed profile if API fails
        const parsed = stored.parsedProfile ?? {};
        setUserProfile(applyStrength({
          avatar: null,
          isOpenToMatches,
          name: parsed.name ?? "",
          email: parsed.email ?? "",
          phone: parsed.phone ?? "",
          headline: parsed.headline ?? "",
          location: parsed.location ?? "",
          bio: parsed.bio ?? "",
          experience: parsed.experience ?? [],
          education: parsed.education ?? [],
          keySkills: parsed.keySkills ?? [],
          experience_years: parsed.experience_years ?? null,
          availability: parsed.availability ?? "",
          preferred_roles: parsed.preferred_roles ?? [],
          certifications: parsed.certifications ?? [],
          additional_information: parsed.additional_information ?? "",
        }, voiceCompleted));
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  const refreshProfile = React.useCallback(async () => {
    if (!candidateId) return;
    try {
      const res = await axios.get(`${API}/candidate/${candidateId}/profile`);
      const data = res.data;
      const vc = loadOnboardingState().voiceIntakeCompleted ?? false;
      setUserProfile((prev) =>
        applyStrength({
          ...prev,
          name: data.name ?? prev.name,
          email: data.email ?? prev.email,
          phone: data.phone ?? prev.phone,
          headline: data.headline ?? prev.headline,
          location: data.location ?? prev.location,
          bio: data.bio ?? prev.bio,
          experience: data.experience?.length ? data.experience : prev.experience,
          education: data.education?.length ? data.education : prev.education,
          keySkills: data.keySkills?.length ? data.keySkills : prev.keySkills,
          experience_years: data.experience_years != null ? data.experience_years : prev.experience_years,
          availability: data.availability ?? prev.availability,
          preferred_roles: data.preferred_roles?.length ? data.preferred_roles : prev.preferred_roles,
          certifications: data.certifications?.length ? data.certifications : prev.certifications,
          additional_information: data.additional_information ?? prev.additional_information,
        }, vc)
      );
    } catch (err) {
      console.error("refreshProfile failed", err);
    }
  }, [candidateId]);

  const handleLogout = React.useCallback(() => {
    clearOnboardingState();
    navigate("/");
  }, [navigate]);

  const handleToggleOpenToMatches = React.useCallback(() => {
    setUserProfile((prev) => {
      const next = { ...prev, isOpenToMatches: !prev.isOpenToMatches };
      const s = loadOnboardingState();
      saveOnboardingState({ ...s, isOpenToMatches: next.isOpenToMatches });
      return next;
    });
  }, []);

  const [chats, setChats] = React.useState([
    { id: "msg-1", sender: "eve", content: "Hi there — great chatting with you. I've got your resume in front of me. Let's fill in a few details that are missing from your profile." },
  ]);

  // Opportunities: count pending (no candidate_response) recruiter-interest notifications
  // plus unread candidate_activity_feed entries
  const [opportunitiesCount, setOpportunitiesCount] = React.useState(0);
  React.useEffect(() => {
    if (!candidateId) return;
    const fetchCount = () => {
      const oppPromise = axios
        .get(`${API}/candidate/${candidateId}/opportunities`)
        .then((res) => (res.data || []).filter((o) => !o.candidate_response).length)
        .catch(() => 0);
      const notifPromise = axios
        .get(`${API}/candidate/${candidateId}/notifications`)
        .then((res) => (res.data || []).filter((n) => !n.is_read).length)
        .catch(() => 0);
      Promise.all([oppPromise, notifPromise]).then(([oppCount, notifCount]) => {
        setOpportunitiesCount(oppCount + notifCount);
      });
    };
    fetchCount();
    const interval = setInterval(fetchCount, 30000);
    return () => clearInterval(interval);
  }, [candidateId]);

  // Show weak-profile popup once profile loads if voice intake is incomplete and strength < 75
  React.useEffect(() => {
    if (!voiceCompleted && userProfile.strengthPercent > 0 && userProfile.strengthPercent < 75) {
      const t = setTimeout(() => setShowWeakProfilePopup(true), 800);
      return () => clearTimeout(t);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userProfile.strengthPercent]);

  // Update greeting once real profile loads
  React.useEffect(() => {
    const firstName = userProfile.name?.split(" ")[0];
    if (!firstName) return;
    setChats((prev) =>
      prev[0]?.id === "msg-1"
        ? [{ ...prev[0], content: `Hi ${firstName} — great chatting with you. I've got your resume in front of me. Let's fill in a few details that are missing from your profile.` }, ...prev.slice(1)]
        : prev
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userProfile.name]);
  const [inputValue, setInputValue] = React.useState("");
  const [availableJobs, setAvailableJobs] = React.useState([]);
  const [documents, setDocuments] = React.useState({ resume: null, certificates: [] });
  const [docsLoading, setDocsLoading] = React.useState(false);
  const [selectedJob, setSelectedJob] = React.useState(null);
  const [sending, setSending] = React.useState(false);

  const [jobsLoading, setJobsLoading] = React.useState(true);
  const [jobsError, setJobsError] = React.useState(false);
  const [centerView, setCenterView] = React.useState("swipe"); // "swipe" | "chat"

  // Load real job recommendations from backend
  const fetchJobs = React.useCallback(() => {
    if (!candidateId) return;
    setJobsError(false);
    axios
      .get(`${API}/candidate/${candidateId}/jobs`)
      .then((res) => {
        setAvailableJobs(res.data || []);
        setSelectedJob((prev) => prev ?? (res.data?.[0] || null));
        setJobsLoading(false);
      })
      .catch(() => {
        setJobsError(true);
        setJobsLoading(false);
      });
  }, [candidateId]);

  React.useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 60000);
    return () => clearInterval(interval);
  }, [fetchJobs]);

  React.useEffect(() => {
    if (!candidateId) return;
    setDocsLoading(true);
    axios
      .get(`${API}/candidate/${candidateId}/documents`)
      .then((res) => setDocuments(res.data))
      .catch(() => {})
      .finally(() => setDocsLoading(false));
  }, [candidateId]);

  const sessionIdRef = React.useRef(
    `sess-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  );

  const handleSendMessage = async (e) => {
    e.preventDefault();
    const text = inputValue.trim();
    if (!text || sending) return;

    const userMsg = { id: `u-${Date.now()}`, sender: "user", content: text };
    const nextChats = [...chats, userMsg];
    setChats(nextChats);
    setInputValue("");
    setSending(true);

    try {
      const historyPayload = nextChats
        .filter((c) => c.sender === "user" || c.sender === "eve")
        .map((c) => ({
          role: c.sender === "user" ? "user" : "assistant",
          content: c.content,
        }));

      const res = await axios.post(`${API}/chat`, {
        messages: historyPayload,
        session_id: sessionIdRef.current,
        candidate_id: candidateId,
      });

      const reply = res?.data?.reply?.trim();
      if (reply) {
        setChats((prev) => [
          ...prev,
          { id: `e-${Date.now()}`, sender: "eve", content: reply },
        ]);
      } else {
        toast.error("Eve didn't respond. Try again?");
      }

      // If LLM returned profile updates, refresh the profile panel
      if (res?.data?.profile_updates && candidateId) {
        await refreshProfile();
      }
    } catch (err) {
      console.error("chat error", err);
      toast.error("Couldn't reach Eve right now.");
    } finally {
      setSending(false);
    }
  };

  const handleResumeReplaced = React.useCallback((filename, newProfile) => {
    setDocuments((prev) => ({ ...prev, resume: { filename } }));
    // Always refresh from DB after resume replace to get the preserved voice fields
    refreshProfile();
  }, [refreshProfile]);

  const handleCertUploaded = React.useCallback((cert) => {
    setDocuments((prev) => ({ ...prev, certificates: [...prev.certificates, cert] }));
  }, []);

  const handleCertReplaced = React.useCallback((certId, filename) => {
    setDocuments((prev) => ({
      ...prev,
      certificates: prev.certificates.map((c) =>
        c.id === certId ? { ...c, filename } : c
      ),
    }));
  }, []);

  const handleResumeDeleted = React.useCallback(() => {
    setDocuments((prev) => ({ ...prev, resume: null }));
  }, []);

  const handleCertDeleted = React.useCallback((certId) => {
    setDocuments((prev) => ({
      ...prev,
      certificates: prev.certificates.filter((c) => c.id !== certId),
    }));
  }, []);

  const handleTrackJob = async (jobId) => {
    const job = availableJobs.find((j) => j.id === jobId);
    if (!job || !candidateId) return;
    const willTrack = !job.tracked;
    // Optimistic update
    setAvailableJobs((prev) =>
      prev.map((j) => (j.id === jobId ? { ...j, tracked: willTrack } : j))
    );
    try {
      if (willTrack) {
        await axios.post(`${API}/candidate/${candidateId}/jobs/${jobId}/track`);
        toast.success(`Tracking ${job.company}`);
      } else {
        await axios.delete(`${API}/candidate/${candidateId}/jobs/${jobId}/track`);
        toast("Untracked");
      }
    } catch {
      // Revert on failure
      setAvailableJobs((prev) =>
        prev.map((j) => (j.id === jobId ? { ...j, tracked: !willTrack } : j))
      );
      toast.error("Could not update tracking.");
    }
  };

  const handleDismissJob = async (jobId) => {
    const job = availableJobs.find((j) => j.id === jobId);
    if (!job || !candidateId) return;
    setAvailableJobs((prev) => prev.filter((j) => j.id !== jobId));
    if (selectedJob?.id === jobId) {
      setSelectedJob(availableJobs.find((j) => j.id !== jobId) || null);
    }
    toast("Removed from feed", { description: "You won't see this again." });
    try {
      await axios.post(`${API}/candidate/${candidateId}/jobs/${jobId}/dismiss`);
    } catch {
      // Silently re-fetch to restore state if dismiss failed
      fetchJobs();
    }
  };

  return (
    <div
      className="h-screen w-full bg-[#FBFBF9] text-[#1F1F1F] overflow-hidden flex flex-col"
      data-testid="app-shell"
    >
      <Toaster position="top-right" richColors closeButton />

      {/* Dashboard top header with Bell */}
      <div className="shrink-0 flex items-center justify-end px-5 py-2 border-b border-black/[0.05]">
        <button
          data-testid="header-bell-btn"
          onClick={() => setActiveTab("opportunities")}
          className="relative p-1.5 rounded-lg text-[#4A4A48] hover:bg-black/[0.04] transition-colors"
          aria-label="Notifications"
        >
          <Bell className="w-[18px] h-[18px]" strokeWidth={1.5} />
          {opportunitiesCount > 0 && (
            <span
              data-testid="header-bell-badge"
              className="absolute -top-0.5 -right-0.5 min-w-[16px] h-[16px] px-1 rounded-full bg-[#1F1F1F] text-white text-[9px] font-medium flex items-center justify-center"
            >
              {opportunitiesCount}
            </span>
          )}
        </button>
      </div>

      {/* Weak profile popup */}
      {showWeakProfilePopup && (
        <div
          data-testid="weak-profile-popup"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
          onClick={() => setShowWeakProfilePopup(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-xl px-8 py-7 max-w-sm w-full mx-4 flex flex-col gap-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3">
              <span className="w-2.5 h-2.5 rounded-full bg-[#E11D48] shrink-0" />
              <p className="text-[15px] font-medium text-[#1F1F1F]">
                Your profile isn't strong yet
              </p>
            </div>
            <p className="text-[13px] text-[#4A4A48] leading-relaxed">
              Your profile strength is {userProfile.strengthPercent}%. Complete a quick voice chat with Eve to unlock better job matches and reach a strong profile.
            </p>
            <div className="flex gap-2">
              <button
                data-testid="weak-profile-chat-btn"
                onClick={() => {
                  setShowWeakProfilePopup(false);
                  navigate("/onboarding?resume_voice=1");
                }}
                className="flex-1 bg-[#1F1F1F] text-white text-[13px] font-medium rounded-full py-2.5 hover:bg-black transition-colors"
              >
                Chat with Eve
              </button>
              <button
                data-testid="weak-profile-dismiss-btn"
                onClick={() => setShowWeakProfilePopup(false)}
                className="flex-1 bg-black/[0.05] text-[#1F1F1F] text-[13px] font-medium rounded-full py-2.5 hover:bg-black/[0.09] transition-colors"
              >
                Maybe later
              </button>
            </div>
          </div>
        </div>
      )}

      <PanelGroup direction="horizontal" className="flex-1 min-h-0">
        <Panel id="left-panel" order={1} defaultSize={18} minSize={12} maxSize={28} className="h-full">
          <Sidebar
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            userProfile={userProfile}
            jobsCount={availableJobs.filter((j) => !j.viewed).length}
            opportunitiesCount={opportunitiesCount}
            recentActivity={MOCK_RECENT_ACTIVITY}
            onLogout={handleLogout}
          />
        </Panel>

        <ResizeHandle testId="resize-handle-left" />

        <Panel id="center-panel" order={2} defaultSize={32} minSize={22} className="h-full">
          <div className="h-full flex flex-col bg-[#FBFBF9] min-h-0">
            {/* Toggle bar */}
            <div className="shrink-0 flex items-center gap-1 px-4 pt-3 pb-2 border-b border-black/[0.05]">
              <button
                onClick={() => setCenterView("swipe")}
                className={`px-3 py-1.5 rounded-lg text-[12.5px] transition-colors ${
                  centerView === "swipe"
                    ? "bg-black/[0.06] text-[#1F1F1F] font-medium"
                    : "text-[#9A9A98] hover:text-[#4A4A48]"
                }`}
              >
                Jobs for you
              </button>
              <button
                onClick={() => {
                  if (!voiceCompleted) {
                    // Resume voice intake from where they left off
                    navigate("/onboarding?resume_voice=1");
                  } else {
                    setCenterView("chat");
                  }
                }}
                className={`px-3 py-1.5 rounded-lg text-[12.5px] transition-colors ${
                  centerView === "chat"
                    ? "bg-black/[0.06] text-[#1F1F1F] font-medium"
                    : "text-[#9A9A98] hover:text-[#4A4A48]"
                }`}
              >
                Chat with Eve
              </button>
            </div>

            {centerView === "swipe" ? (
              jobsLoading ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="flex items-center gap-2 text-[#9A9A98]">
                    <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" />
                    <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" style={{ animationDelay: "120ms" }} />
                    <span className="w-2 h-2 rounded-full bg-[#B5B5B3] animate-pulse" style={{ animationDelay: "240ms" }} />
                  </div>
                </div>
              ) : jobsError ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-3 px-8 text-center">
                  <p className="text-[14px] text-[#1F1F1F]">Couldn't load recommendations</p>
                  <button
                    onClick={fetchJobs}
                    className="text-[12.5px] text-[#4A4A48] underline underline-offset-2 hover:text-[#1F1F1F]"
                  >
                    Try again
                  </button>
                </div>
              ) : (
                <SwipeJobDeck
                  jobs={availableJobs}
                  candidateId={candidateId}
                  onJobsChange={fetchJobs}
                />
              )
            ) : (
              <ChatHub
                chats={chats}
                inputValue={inputValue}
                setInputValue={setInputValue}
                onSend={handleSendMessage}
                sending={sending}
                quickActions={QUICK_ACTIONS}
              />
            )}
          </div>
        </Panel>

        <ResizeHandle testId="resize-handle-right" subtle />

        <Panel id="right-panel" order={3} defaultSize={50} minSize={30} className="h-full">
          <LivingProfile
            activeTab={activeTab}
            userProfile={userProfile}
            jobs={availableJobs}
            documents={documents}
            docsLoading={docsLoading}
            candidateId={candidateId}
            selectedJob={selectedJob}
            setSelectedJob={setSelectedJob}
            onTrackJob={handleTrackJob}
            onDismissJob={handleDismissJob}
            onToggleOpenToMatches={handleToggleOpenToMatches}
            onResumeReplaced={handleResumeReplaced}
            onCertUploaded={handleCertUploaded}
            onCertReplaced={handleCertReplaced}
            onResumeDeleted={handleResumeDeleted}
            onCertDeleted={handleCertDeleted}
            onInterested={fetchJobs}
            onJobViewed={(jobId) =>
              setAvailableJobs((prev) =>
                prev.map((j) => (j.id === jobId ? { ...j, viewed: true } : j))
              )
            }
          />
        </Panel>
      </PanelGroup>
    </div>
  );
}

export default function DashboardGuard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const candidateId = React.useMemo(() => {
    const fromUrl = searchParams.get("candidate_id");
    console.log("[LinkedIn] current URL:", window.location.href);
    console.log("[LinkedIn] candidate_id:", fromUrl);
    console.log("[LinkedIn] persisted state:", loadOnboardingState());
    if (fromUrl) {
      const s = loadOnboardingState();
      saveOnboardingState({ ...s, linkedInAuthenticated: true, candidateId: fromUrl });
      return fromUrl;
    }
    return loadOnboardingState().candidateId ?? null;
  }, [searchParams]);

  React.useEffect(() => {
    if (candidateId) {
      if (searchParams.get("candidate_id")) {
        console.log("[LinkedIn] redirecting to: /dashboard");
        navigate("/dashboard", { replace: true });
      }
      return;
    }
    navigate("/", { replace: true });
  }, [candidateId, navigate, searchParams]);

  if (!candidateId) return null;
  return <Dashboard />;
}
