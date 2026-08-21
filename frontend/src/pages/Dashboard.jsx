import React from "react";
import axios from "axios";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { Toaster, toast } from "sonner";
import { Bell } from "lucide-react";

import Sidebar from "../components/Sidebar";
import ChatHub from "../components/ChatHub";
import LivingProfile from "../components/LivingProfile";
import SwipeJobDeck from "../components/SwipeJobCard";
import CandidateSettingsModal from "../components/CandidateSettingsModal";
import {
  MOCK_RECENT_ACTIVITY,
  QUICK_ACTIONS,
} from "../mock";
import { loadOnboardingState, saveOnboardingState, clearOnboardingState } from "../lib/onboardingStorage";
import { buildDashboardEveGreetingFromProfile } from "../lib/dashboardMessaging";
import { hydrateProfileStrength } from "../lib/profileStrength";
import { normalizeProfileForDisplay } from "../lib/profileNormalization";
import { useNavigate, useSearchParams } from "react-router-dom";
import VoiceIntake from "../components/onboarding/VoiceIntake";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

export function getVoiceIntakeCenterView(voiceIntakeResume) {
  const currentQuestion = voiceIntakeResume?.current_question || "";
  const isInProgress =
    voiceIntakeResume?.status === "in_progress" &&
    Boolean(voiceIntakeResume?.has_open_question) &&
    Boolean(currentQuestion);

  if (isInProgress) return "chat";
  if (voiceIntakeResume?.status === "completed") return "swipe";
  return null;
}

function buildFallbackProfile(isOpenToMatches = true) {
  return {
    candidate_id: null,
    candidateId: null,
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
    voice_intake_resume: null,
  };
}

function hydrateDisplayProfile(profile) {
  return hydrateProfileStrength(normalizeProfileForDisplay(profile));
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
  const [candidateToken] = React.useState(() => loadOnboardingState().candidateToken ?? null);
  const isOpenToMatches = stored.isOpenToMatches ?? true;
  const [settingsOpen, setSettingsOpen] = React.useState(false);

  const [showWeakProfilePopup, setShowWeakProfilePopup] = React.useState(false);

  const [activeTab, setActiveTab] = React.useState(() => {
    if (stored.newlyOnboarded) {
      saveOnboardingState({ ...stored, newlyOnboarded: false });
      return "profile";
    }
    return "new-jobs";
  });

  const [userProfile, setUserProfile] = React.useState(() =>
    hydrateDisplayProfile(buildFallbackProfile(isOpenToMatches))
  );
  const voiceIntakeResume = userProfile.voice_intake_resume;
  const voiceIntakeResumeQuestion = voiceIntakeResume?.current_question || voiceIntakeResume?.next_question || "";
  const voiceIntakeCurrentQuestion = voiceIntakeResume?.current_question || "";
  const voiceIntakeInProgress =
    voiceIntakeResume?.status === "in_progress" &&
    Boolean(voiceIntakeResume?.has_open_question) &&
    Boolean(voiceIntakeCurrentQuestion);
  const voiceIntakeCompleted = voiceIntakeResume?.status === "completed";
  const voiceIntakeCenterView = getVoiceIntakeCenterView(voiceIntakeResume);

  // All state declarations up front so effects can reference them
  const [chats, setChats] = React.useState([
    { id: "msg-1", sender: "eve", content: "Hi there — I'm Eve, your career partner on Pontis. How can I help you today?" },
  ]);
  const [chatRestored, setChatRestored] = React.useState(false);
  const [inputValue, setInputValue] = React.useState("");
  const [availableJobs, setAvailableJobs] = React.useState([]);
  const [documents, setDocuments] = React.useState({ resume: null, certificates: [] });
  const [docsLoading, setDocsLoading] = React.useState(false);
  const [selectedJob, setSelectedJob] = React.useState(null);
  const [sending, setSending] = React.useState(false);
  const [jobsLoading, setJobsLoading] = React.useState(true);
  const [jobsError, setJobsError] = React.useState(false);
  const [centerView, setCenterView] = React.useState("swipe"); // "swipe" | "chat" | "voice"
  const [opportunitiesCount, setOpportunitiesCount] = React.useState(0);

  // Load real profile from PostgreSQL on mount
  React.useEffect(() => {
    if (!candidateId) return;
    console.log("Current candidateId:", candidateId);
    axios
      .get(`${API}/candidate/${candidateId}/profile`)
      .then((res) => {
        const data = res.data;
        const profileCandidateId = data.candidate_id ?? data.candidateId ?? candidateId ?? null;
        if (profileCandidateId && profileCandidateId !== candidateId) {
          setCandidateId(profileCandidateId);
          saveOnboardingState({ ...loadOnboardingState(), candidateId: profileCandidateId });
        }
        setUserProfile(hydrateDisplayProfile({
          candidate_id: profileCandidateId,
          candidateId: profileCandidateId,
          avatar: data.photo_url ?? null,
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
          voice_intake_resume: data.voice_intake_resume ?? null,
          profile_strength_percent: data.profile_strength_percent ?? data.strengthPercent,
          profile_strength_label: data.profile_strength_label ?? data.strength,
        }));
      })
      .catch(() => {
        const parsed = stored.parsedProfile ?? {};
        setUserProfile(hydrateDisplayProfile({
          candidate_id: candidateId,
          candidateId,
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
          voice_intake_resume: parsed.voice_intake_resume ?? null,
          profile_strength_percent: parsed.profile_strength_percent ?? parsed.strengthPercent,
          profile_strength_label: parsed.profile_strength_label ?? parsed.strength,
        }));
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  React.useEffect(() => {
    if (voiceIntakeCenterView) {
      setCenterView(voiceIntakeCenterView);
    }
  }, [voiceIntakeCenterView]);

  const refreshProfile = React.useCallback(async () => {
    if (!candidateId) return;
    try {
      const res = await axios.get(`${API}/candidate/${candidateId}/profile`);
      const data = res.data;
      const profileCandidateId = data.candidate_id ?? data.candidateId ?? candidateId ?? null;
      if (profileCandidateId && profileCandidateId !== candidateId) {
        setCandidateId(profileCandidateId);
        saveOnboardingState({ ...loadOnboardingState(), candidateId: profileCandidateId });
      }
      const hasPhotoUrl = Object.prototype.hasOwnProperty.call(data || {}, "photo_url");
      setUserProfile((prev) =>
        hydrateDisplayProfile({
          ...prev,
          candidate_id: profileCandidateId ?? prev.candidate_id ?? prev.candidateId ?? null,
          candidateId: profileCandidateId ?? prev.candidate_id ?? prev.candidateId ?? null,
          avatar: hasPhotoUrl ? (data.photo_url ?? null) : prev.avatar,
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
          voice_intake_resume: data.voice_intake_resume ?? prev.voice_intake_resume ?? null,
          profile_strength_percent: data.profile_strength_percent ?? data.strengthPercent ?? prev.profile_strength_percent,
          profile_strength_label: data.profile_strength_label ?? data.strength ?? prev.strength,
        })
      );
    } catch (err) {
      console.error("refreshProfile failed", err);
    }
  }, [candidateId]);

  const handlePhotoChange = React.useCallback((url) => {
    setUserProfile((prev) => ({ ...prev, avatar: url }));
    refreshProfile();
  }, [refreshProfile]);

  const handleLogout = React.useCallback(() => {
    clearOnboardingState();
    navigate("/");
  }, [navigate]);

  const handleDeleteSuccess = React.useCallback(() => {
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

  // Restore persisted chat history from backend on mount
  React.useEffect(() => {
    if (!candidateId) return;
    axios
      .get(`${API}/candidate/${candidateId}/chat`)
      .then((res) => {
        const msgs = res.data?.messages;
        if (!Array.isArray(msgs) || msgs.length === 0) return;
        const restored = msgs.map((m, i) => ({
          id: `r-${i}`,
          sender: m.role === "user" ? "user" : "eve",
          content: m.content,
        }));
        setChats(restored);
        setChatRestored(true);
      })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  // Update greeting once real profile loads - personalise with name, never ask for already-known info
  React.useEffect(() => {
    if (chatRestored) return;
    const firstName = userProfile.name?.split(" ")[0];
    const hasResume = documents?.resume != null;
    const profileComplete = userProfile.headline && userProfile.keySkills?.length > 0;
    const greeting = buildDashboardEveGreetingFromProfile({
      firstName,
      profileComplete,
      hasResume,
      voiceIntakeResume,
    });
    if (!greeting) return;
    setChats((prev) =>
      prev[0]?.id === "msg-1"
        ? [{ ...prev[0], content: greeting }, ...prev.slice(1)]
        : prev
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatRestored, voiceIntakeInProgress, voiceIntakeResumeQuestion, userProfile.name, userProfile.headline, userProfile.keySkills?.length, documents?.resume]);
  // Opportunities count
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
    if (!voiceIntakeCompleted && userProfile.strengthPercent > 0 && userProfile.strengthPercent < 75) {
      const t = setTimeout(() => setShowWeakProfilePopup(true), 800);
      return () => clearTimeout(t);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userProfile.strengthPercent, voiceIntakeCompleted]);

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
                  setCenterView(voiceIntakeInProgress || voiceIntakeCompleted ? "chat" : "voice");
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
            onSettings={() => setSettingsOpen(true)}
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
                  if (voiceIntakeInProgress) {
                    setCenterView("chat");
                  } else if (!voiceIntakeCompleted) {
                    setCenterView("voice");
                  } else {
                    setCenterView("chat");
                  }
                }}
                className={`px-3 py-1.5 rounded-lg text-[12.5px] transition-colors ${
                  centerView === "chat" || centerView === "voice"
                    ? "bg-black/[0.06] text-[#1F1F1F] font-medium"
                    : "text-[#9A9A98] hover:text-[#4A4A48]"
                }`}
              >
                Chat with Eve
              </button>
            </div>

            {centerView === "voice" ? (
              <div className="flex-1 overflow-y-auto eve-scroll px-6 py-8">
              <VoiceIntake
                  firstName={userProfile.name?.split(" ")[0] || "there"}
                  candidateId={candidateId}
                  candidateProfile={userProfile}
                  onComplete={(result) => {
                    const s = loadOnboardingState();
                    const completed =
                      result?.status === "completed" || result?.status === "duplicate";
                    saveOnboardingState({ ...s, voiceIntakeCompleted: completed });
                    refreshProfile();
                  }}
                />
              </div>
            ) : centerView === "swipe" ? (
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
            onPhotoChange={handlePhotoChange}
            onJobViewed={(jobId) =>
              setAvailableJobs((prev) =>
                prev.map((j) => (j.id === jobId ? { ...j, viewed: true } : j))
              )
            }
          />
        </Panel>
      </PanelGroup>
      <CandidateSettingsModal
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        candidateId={candidateId}
        candidateToken={candidateToken}
        candidateName={userProfile.name}
        candidateEmail={userProfile.email}
        onDeleteSuccess={handleDeleteSuccess}
      />
    </div>
  );
}

export default function DashboardGuard() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const candidateId = React.useMemo(() => {
    const fromUrl = searchParams.get("candidate_id");
    const candidateToken = searchParams.get("candidate_token");
    console.log("[LinkedIn] current URL:", window.location.href);
    console.log("[LinkedIn] candidate_id:", fromUrl);
    console.log("[LinkedIn] persisted state:", loadOnboardingState());
    if (fromUrl) {
      const s = loadOnboardingState();
      saveOnboardingState({ ...s, linkedInAuthenticated: true, candidateId: fromUrl, candidateToken: candidateToken || s.candidateToken || null });
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
